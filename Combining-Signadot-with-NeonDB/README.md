# True "Branch-Based Environments": Combining Signadot Sandboxes with Neon DB Branching

Ephemeral sandbox environments solve many problems for microservices teams. You can spin up an isolated copy of your service, test your changes, and tear it down. No conflicts with other developers. No waiting for a shared staging slot.

But here's the catch: your sandbox service still connects to the same staging database as everyone else. One developer's test writes pollute another's queries. Schema migrations break active tests. Seed data disappears mid-run. The application layer is isolated, but the data layer is not.

This guide shows you how to fix that problem. You will combine Signadot Sandboxes with Neon's database branching to create true full-stack isolation. Every sandbox gets its own application fork _and_ its own database branch. When the sandbox dies, the database branch dies with it.

## What You Will Build

The end-to-end system works as follows:

1.  A developer creates a Signadot Sandbox
2.  A Resource Plugin automatically creates a Neon database branch and stores the connection string in a new Kubernetes Secret
3.  The sandbox pod starts with a connection string pointing to the isolated branch
4.  The developer runs tests against isolated data
5.  The developer deletes the sandbox
6.  The Resource Plugin deletes the Neon branch and the Kubernetes Secret automatically

No shared state. No test pollution. No manual cleanup scripts.

## Prerequisites

Before you begin, ensure you have:

-   A [Signadot account](https://www.signadot.com/) with a connected Kubernetes cluster
-   The [Signadot operator and CLI](https://www.signadot.com/docs/getting-started/installation) installed and authenticated
-   A [Neon account](https://neon.com/) with an existing project containing a database
-   The Neon CLI (`neonctl`) installed
-   A Neon API key
-   `kubectl` configured to access your cluster
-   A microservice that connects to a Postgres database

## Step 1: Configure Your Service

Your microservice needs to read its database connection string from an environment variable sourced from a Kubernetes Secret. You need this pattern because it lets you swap connection strings at runtime without rebuilding container images. The baseline deployment connects to the main database, while sandboxed forks can connect to different databases by using different Secrets.

### The Microservice Application

Here is a simple Node.js service that manages users:

**`app.js`:**

```jsx
// app.js
const express = require('express');
const { Pool } = require('pg');

const app = express();
app.use(express.json());

// Read connection string from environment variable
const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: { rejectUnauthorized: false },
});

// Health check endpoint
app.get('/health', async (req, res) => {
  try {
    await pool.query('SELECT 1');
    res.json({ status: 'healthy' });
  } catch (err) {
    res.status(500).json({ status: 'unhealthy', error: err.message });
  }
});

// Get all users
app.get('/users', async (req, res) => {
  const result = await pool.query('SELECT * FROM users ORDER BY id');
  res.json(result.rows);
});

// Create a user
app.post('/users', async (req, res) => {
  const { name, email } = req.body;
  const result = await pool.query(
    'INSERT INTO users (name, email) VALUES ($1, $2) RETURNING *',
    [name, email]
  );
  res.status(201).json(result.rows[0]);
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});

```

The key line is `process.env.DATABASE_URL`:

-   The service does not hardcode any database credentials
-   It reads the connection string from the environment at runtime
-   Kubernetes injects this value from a Secret

### The Kubernetes Deployment

The Deployment pulls `DATABASE_URL` from a Secret named `users-db-credentials`:

**`deployment.yaml`:**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: users-service
  namespace: default
  labels:
    app: users-service
spec:
  replicas: 1
  selector:
    matchLabels:
      app: users-service
  template:
    metadata:
      labels:
        app: users-service
      annotations:
        # Enable Signadot routing sidecar
        sidecar.signadot.com/inject: "true"
    spec:
      containers:
        - name: users-service
          image: ghcr.io/emminex23/users-service:latest
          ports:
            - containerPort: 3000
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: users-db-credentials
                  key: DATABASE_URL
          resources:
            requests:
              memory: "128Mi"
              cpu: "100m"
            limits:
              memory: "256Mi"
              cpu: "500m"
---
apiVersion: v1
kind: Service
metadata:
  name: users-service
  namespace: default
spec:
  selector:
    app: users-service
  ports:
    - port: 3000
      targetPort: 3000

```

The important parts:

-   `secretKeyRef` tells Kubernetes to read the value from a Secret
-   `name: users-db-credentials` specifies which Secret to use
-   `key: DATABASE_URL` specifies which key within the Secret

### The Baseline Secret

Create a Secret containing the connection string to your main Neon branch. You need this Secret for the baseline deployment that runs when no sandbox is active.

**`db-secret.yaml`:**

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: users-db-credentials
  namespace: default
type: Opaque
stringData:
  DATABASE_URL: "postgresql://neondb_owner:npg_w96GqDPdoVCc@ep-ancient-sunset-ahaw7ysn-pooler.c-3.us-east-1.aws.neon.tech/appdb?sslmode=require&channel_binding=require"

```

Replace the `DATABASE_URL` value with your actual Neon connection string.

Apply the secret and deployment:

```bash
kubectl apply -f db-secret.yaml
kubectl apply -f deployment.yaml

```

-   The first command creates the Secret in your cluster
-   The second command deploys the service, which reads from that Secret

Verify the pods are running:

```bash
kubectl get pods -l app=users-service

```

![Verify the pods are running](./images/image-001.png)

The `2/2` indicates that both the main container and the Signadot routing sidecar are running.

### Test the Baseline Service

Port-forward to test the service locally:

```bash
kubectl port-forward svc/users-service 3000:3000

```

![Port-forwarding](./images/image-002.png)

In a separate terminal, test the endpoints:

```bash
# Health check
curl <http://localhost:3000/health>

```

![Health check](./images/image-003.png)

```bash
# List users
curl <http://localhost:3000/users>

```

![List users](./images/image-004.png)

Stop the port-forward with `Ctrl+C`.

## Step 2: Create a Signadot Resource Plugin

A Signadot Resource Plugin is the "glue" that connects Signadot to Neon. You need a Resource Plugin because Signadot doesn't natively know how to create Neon branches. The plugin runs custom scripts during sandbox lifecycle events:

-   **On create**: Creates a Neon branch and stores the connection string in a new Kubernetes Secret
-   **On delete**: Deletes the Neon branch and the Kubernetes Secret

### Store the Neon API Key

The Resource Plugin runner needs access to your Neon API key. You store it in a Kubernetes Secret so the runner pod can authenticate with the Neon API.

```bash
kubectl create secret generic neon-api-credentials \\
    --namespace=default \\
    --from-literal=NEON_API_KEY=your-neon-api-key-here

```

-   `neon-api-credentials` is the name of the Secret
-   `NEON_API_KEY` is the key that the runner will look for
-   Replace `your-neon-api-key-here` with your actual Neon API key

Verify the secret:

```bash
kubectl get secret neon-api-credentials -n default

```
![Verify the secret](./images/image-005.png)

### Define the Resource Plugin

Create a file named `neon-branch-plugin.yaml`:

```yaml
name: neon-branch
spec:
  description: Creates and deletes Neon database branches for sandbox isolation
  
  runner:
    image: debian:bookworm-slim
    namespace: default
    podTemplateOverlay: |
      spec:
        serviceAccountName: neon-branch-plugin-sa
        containers:
          - name: main
            env:
              - name: NEON_API_KEY
                valueFrom:
                  secretKeyRef:
                    name: neon-api-credentials
                    key: NEON_API_KEY

  create:
    - name: createbranch
      inputs:
        - name: project-id
          valueFromSandbox: true
          as:
            env: NEON_PROJECT_ID
        - name: parent-branch
          valueFromSandbox: true
          as:
            env: PARENT_BRANCH
        - name: database-name
          valueFromSandbox: true
          as:
            env: DATABASE_NAME
        - name: namespace
          valueFromSandbox: true
          as:
            env: TARGET_NAMESPACE
      script: |
        #!/bin/bash
        set -e

        # Install required packages
        apt-get update && apt-get install -y curl ca-certificates gnupg

        # Install Node.js
        curl -fsSL <https://deb.nodesource.com/setup_20.x> | bash -
        apt-get install -y nodejs

        # Install kubectl
        curl -LO "<https://dl.k8s.io/release/$>(curl -L -s <https://dl.k8s.io/release/stable.txt>)/bin/linux/amd64/kubectl"
        chmod +x kubectl
        mv kubectl /usr/local/bin/

        # Install neonctl
        npm install -g neonctl

        # Generate a unique branch name and secret name using the sandbox name
        # Remove any hyphens from sandbox name for Neon branch naming
        SAFE_NAME=$(echo "${SIGNADOT_SANDBOX_NAME}" | tr -d '-')
        BRANCH_NAME="sandbox${SAFE_NAME}"
        SECRET_NAME="dbcreds${SAFE_NAME}"

        echo "Creating Neon branch: ${BRANCH_NAME}"
        echo "Project ID: ${NEON_PROJECT_ID}"
        echo "Parent branch: ${PARENT_BRANCH}"

        # Create the branch
        neonctl branches create \\
          --project-id "${NEON_PROJECT_ID}" \\
          --name "${BRANCH_NAME}" \\
          --parent "${PARENT_BRANCH}" \\
          --output json > /tmp/branch-output.json

        cat /tmp/branch-output.json

        # Extract the branch ID
        BRANCH_ID=$(cat /tmp/branch-output.json | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
        echo "Created branch ID: ${BRANCH_ID}"

        # Get the connection string for the new branch
        CONNECTION_STRING=$(neonctl connection-string "${BRANCH_NAME}" \\
          --project-id "${NEON_PROJECT_ID}" \\
          --database-name "${DATABASE_NAME}")

        echo "Connection string retrieved successfully"

        # Create a Kubernetes Secret with the connection string
        echo "Creating Kubernetes Secret: ${SECRET_NAME} in namespace ${TARGET_NAMESPACE}"
        kubectl create secret generic "${SECRET_NAME}" \\
          --namespace="${TARGET_NAMESPACE}" \\
          --from-literal=DATABASE_URL="${CONNECTION_STRING}"

        echo "Kubernetes Secret created successfully"

        # Write outputs to files for Signadot to capture
        mkdir -p /outputs
        echo -n "${BRANCH_NAME}" > /outputs/branch-name
        echo -n "${BRANCH_ID}" > /outputs/branch-id
        echo -n "${SECRET_NAME}" > /outputs/secret-name
        echo -n "${CONNECTION_STRING}" > /outputs/connection-string

        echo "Branch creation complete"

      outputs:
        - name: branch-name
          valueFromPath: /outputs/branch-name
        - name: branch-id
          valueFromPath: /outputs/branch-id
        - name: secret-name
          valueFromPath: /outputs/secret-name
        - name: connection-string
          valueFromPath: /outputs/connection-string

  delete:
    - name: deletebranch
      inputs:
        - name: project-id
          valueFromSandbox: true
          as:
            env: NEON_PROJECT_ID
        - name: namespace
          valueFromSandbox: true
          as:
            env: TARGET_NAMESPACE
        - name: branch-name
          valueFromStep:
            name: createbranch
            output: branch-name
          as:
            env: BRANCH_NAME
        - name: secret-name
          valueFromStep:
            name: createbranch
            output: secret-name
          as:
            env: SECRET_NAME
      script: |
        #!/bin/bash
        set -e

        # Install required packages
        apt-get update && apt-get install -y curl ca-certificates gnupg

        # Install Node.js
        curl -fsSL <https://deb.nodesource.com/setup_20.x> | bash -
        apt-get install -y nodejs

        # Install kubectl
        curl -LO "<https://dl.k8s.io/release/$>(curl -L -s <https://dl.k8s.io/release/stable.txt>)/bin/linux/amd64/kubectl"
        chmod +x kubectl
        mv kubectl /usr/local/bin/

        # Install neonctl
        npm install -g neonctl

        echo "Deleting Kubernetes Secret: ${SECRET_NAME}"
        kubectl delete secret "${SECRET_NAME}" --namespace="${TARGET_NAMESPACE}" --ignore-not-found=true

        echo "Deleting Neon branch: ${BRANCH_NAME}"
        neonctl branches delete "${BRANCH_NAME}" \\
          --project-id "${NEON_PROJECT_ID}"

        echo "Cleanup complete"

```

The plugin has three main sections:

1.  **`runner`**: Specifies the container image (`debian:bookworm-slim`) which provides a minimal Linux environment. The script installs Node.js, kubectl, and neonctl at runtime. The `serviceAccountName` references a ServiceAccount with permissions to create and delete Secrets. The Neon API key is injected via `podTemplateOverlay`.
2.  **`create`**: Defines the workflow that runs when a sandbox starts. The step:
    -   Accepts four inputs from the sandbox specification: `project-id`, `parent-branch`, `database-name`, and `namespace`
    -   Installs Node.js, `kubectl`, and `neonctl`
    -   Sanitizes the sandbox name by removing hyphens (Neon branch names work best with alphanumeric characters)
    -   Creates a branch with a name derived from `SIGNADOT_SANDBOX_NAME` (a built-in variable)
    -   Retrieves the connection string for the new branch
    -   Creates a Kubernetes Secret named `dbcreds<sandbox-name>` containing the connection string
    -   Writes outputs including the secret name for later reference
3.  **`delete`**: Defines the workflow that runs when a sandbox is deleted. The step:
    -   Installs the same tools (the delete step runs in a fresh container)
    -   Reads the branch name and secret name from the create step's outputs (using `valueFromStep`)
    -   Deletes the Kubernetes Secret
    -   Deletes the Neon branch

### Create the ServiceAccount and RBAC

The Resource Plugin runner needs permissions to create and delete Secrets in Kubernetes. Create a ServiceAccount with the necessary RBAC permissions.

Create a file named `neon-plugin-rbac.yaml`:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: neon-branch-plugin-sa
  namespace: default
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: neon-branch-plugin-role
  namespace: default
rules:
  - apiGroups: [""]
    resources: ["secrets"]
    verbs: ["create", "delete", "get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: neon-branch-plugin-rolebinding
  namespace: default
subjects:
  - kind: ServiceAccount
    name: neon-branch-plugin-sa
    namespace: default
roleRef:
  kind: Role
  name: neon-branch-plugin-role
  apiGroup: rbac.authorization.k8s.io


```

Apply the RBAC configuration:

```bash
kubectl apply -f neon-plugin-rbac.yaml

```

Verify the ServiceAccount exists:

```bash
kubectl get serviceaccount neon-branch-plugin-sa -n default

```

![Verify the ServiceAccount exists](./images/image-006.png)

### Install the Resource Plugin

Apply the plugin to your Signadot organization:

```bash
signadot resourceplugin apply -f neon-branch-plugin.yaml

```

Verify the plugin exists:

```bash
signadot resourceplugin list

```

![Verify the plugin exists](./images/image-007.png)

## Step 3: Configure the Signadot Sandbox

Now you need a sandbox specification that ties everything together. The sandbox specification tells Signadot:

1.  Which Resource Plugin to invoke and with what parameters
2.  Which Deployment to fork
3.  How to inject the branch's connection string into the forked pod

Create a file named `users-sandbox.yaml`:

```yaml
name: "@{sandbox-name}"
spec:
  description: "Users service sandbox with isolated Neon database branch"
  cluster: "@{cluster}"

  labels:
    team: platform
    service: users-service

  resources:
    - name: usersDb
      plugin: neon-branch
      params:
        project-id: "@{neon-project-id}"
        parent-branch: "main"
        database-name: "appdb"
        namespace: "default"

  forks:
    - forkOf:
        kind: Deployment
        namespace: default
        name: users-service
      customizations:
        env:
          - name: DATABASE_URL
            valueFrom:
              resource:
                name: usersDb
                outputKey: createbranch.connection-string

  defaultRouteGroup:
    endpoints:
      - name: users-api
        target: <http://users-service.default.svc:3000>

```

From the above:

**`resources`**: Tells Signadot to invoke the `neon-branch` plugin when creating this sandbox. The `params` block passes values to the plugin's inputs:

-   `project-id`: Your Neon project ID (passed at apply time)
-   `parent-branch`: The branch to copy from (`main`)
-   `database-name`: The database name within the project (`appdb`)
-   `namespace`: The Kubernetes namespace where the Secret will be created (`default`)

**`forks`**: Creates a copy of the `users-service` Deployment. The `customizations.env` block overrides the `DATABASE_URL` environment variable. The forked pod reads the connection string from the resource plugin's output, which matches the value stored in the Kubernetes Secret.

The `outputKey` follows the format `<step-name>.<output-name>`. Here, `createbranch.connection-string` refers to the `connection-string` output from the `createbranch` step.

**`defaultRouteGroup`**: Creates a preview URL that routes traffic to your sandboxed service.

## Step 4: See It Work

Now walk through the full workflow to verify everything works.

### Create the Sandbox

Find your Signadot cluster name first:

```bash
signadot cluster list

```
![Find your Signadot cluster name](./images/image-008.png)

Apply the sandbox specification with your specific values:

```bash
signadot sandbox apply -f users-sandbox.yaml \\
  --set sandbox-name=featureuserexport \\
  --set cluster=test-cluster \\
  --set neon-project-id=crimson-voice-12345678

```

-   `-set sandbox-name=featureuserexport` names your sandbox
-   `-set cluster=test-cluster` specifies which Kubernetes cluster to use
-   `-set neon-project-id=crimson-voice-12345678` passes your Neon project ID to the Resource Plugin (use your actual Neon project ID)

Expected output:

![Apply the sandbox specification](./images/image-009.png)

Save the preview URL. You'll use it to test the sandbox.

### View the Resource Plugin Logs

While the sandbox is being created, Signadot runs the Resource Plugin's `create` workflow. You can observe this in real-time.

Now, let’s check what the Resource Plugin did by viewing its logs. You should see output confirming branch and Secret creation:

![Resource Plugin logs](./images/image-010.png)

### Verify the Neon Branch Was Created

Open the Neon UI and confirm the branch exists:

![Verify the Neon Branch Was Created](./images/image-011.png)

You can also confirm the branch exists using the command line:

```bash
neonctl branches list --project-id crimson-scene-56898541

```
Use your actual Project ID in place of `crimson-scene-56898541`.

Expected output:
![confirm the branch exists](./images/image-012.png)

-   The `main` branch is your original database
-   The `sandboxfeatureuserexport` branch is the copy created for your sandbox

### Verify the Kubernetes Secret Was Created

List the secrets to confirm the new Secret exists:

```bash
kubectl get secrets -n default | grep dbcreds

```

Expected output:
![List the secrets](./images/image-013.png)

A new Secret named `dbcredsfeatureuserexport` has been created. The naming convention is `dbcreds<sandbox-name>`.

### Verify the Sandbox Pod Uses the Correct Connection String

The `DATABASE_URL` value is base64-encoded. Decode it to see the connection string:

```bash
kubectl get secret dbcredsfeatureuserexport -n default \\
  -o jsonpath='{.data.DATABASE_URL}' | base64 --decode

```

Expected Output:

![Verify the Sandbox Pod](./images/image-014.png)

The Secret contains the connection string for the sandbox branch endpoint (`ep-autumn-scene-ahrmffj2`), not the main branch.

![Secret contains the connection string](./images/image-015.png)

Compare with the baseline Secret:

```bash
kubectl get secret users-db-credentials -n default \\
  -o jsonpath='{.data.DATABASE_URL}' | base64 --decode

```

Expected Output:

![Compare with the baseline Secret](./images/image-016.png)

The baseline Secret points to the main branch endpoint (`ep-ancient-sunset-ahaw7ysn`). Each sandbox gets its own unique Secret with its own connection string.

### Query Existing Data Through the Sandbox

The sandbox branch inherits all data from its parent. Verify this by querying the users.

Get your Signadot API key and query the sandbox endpoint:

```bash
curl -H "signadot-api-key: your-signadot-api-key" \\
  "<https://users-api--featureuserexport.preview.signadot.com/users>"

```

Output:
![query the sandbox endpoint](./images/image-017.png)

The three users from the main branch appear in the sandbox. The copy-on-write branch contains identical data.

### Write Test Data to the Sandbox

Create a new user that exists only in the sandbox branch:

```bash
curl -X POST \\
  -H "signadot-api-key: your-signadot-api-key" \\
  -H "Content-Type: application/json" \\
  -d '{"name": "Sandbox User", "email": "sandbox@test.example"}' \\
  "<https://users-api--featureuserexport.preview.signadot.com/users>"

```

Output:
![Create a new user](./images/image-018.png)

Query the sandbox again:

```bash
curl -H "signadot-api-key: your-signadot-api-key" \\
  "<https://users-api--featureuserexport.preview.signadot.com/users>"

```

Output:
![Query the sandbox again](./images/image-019.png)

The sandbox now contains four users.

### Query the Sandbox Branch Directly

Connect directly to the Neon branch using `psql` to confirm the data exists at the database level.

Get the sandbox branch connection string:

```bash
neonctl connection-string sandboxfeatureuserexport \\
  --project-id crimson-voice-12345678 \\
  --database-name appdb

```

Output:
![Get the sandbox branch connection string](./images/image-020.png)

Connect with psql:

```bash
psql "postgresql://username:password@ep-autumn-scene-ahrmffj2.c-3.us-east-1.aws.neon.tech/appdb?sslmode=require&channel_binding=require"

```

Query all users:

```sql
SELECT * FROM users ORDER BY id;

```

Output:
![Query all users](./images/image-021.png)

The sandbox branch contains all four users, including the test user. Exit psql:

```sql
\\q

```

### Verify the Main Branch is Unaffected

Connect directly to the main branch and confirm it does NOT contain the sandbox test user. Get the main branch connection string:

```bash
neonctl connection-string main \\
  --project-id crimson-voice-12345678 \\
  --database-name appdb

```

Output:
![Verify the Main Branch is Unaffected](./images/image-022.png)

Connect with psql:

```bash
psql "postgresql://username:password@ep-ancient-sunset-ahaw7ysn.c-3.us-east-1.aws.neon.tech/appdb?sslmode=require&channel_binding=require"

```

Query all users:

```sql
SELECT * FROM users ORDER BY id;

```

Output:
![Query all users](./images/image-023.png)

The main branch contains only three users. The test user does not exist here. Search explicitly for the sandbox user:

```sql
SELECT * FROM users WHERE email = 'sandbox@test.example';

```

Output:
![Search explicitly for the sandbox user](./images/image-024.png)

Zero rows. The main branch remains completely clean.

Exit psql:

```sql
\\q

```

**Data isolation is confirmed.** The sandbox branch and main branch operate independently. Changes in one do not affect the other.

### Delete the Sandbox

When testing is complete, delete the sandbox:

```sql
signadot sandbox delete featureuserexport

```

Output:
![Delete the Sandbox](./images/image-025.png)
Signadot runs the Resource Plugin's `delete` workflow automatically. The workflow removes the Kubernetes Secret and Neon branch.

Confirm that the sandbox-specific Secret no longer exists:

```bash
kubectl get secret dbcredsfeatureuserexport -n default

```

Output:
![Confirm that the sandbox-specific Secret no longer exists](./images/image-026.png)

The Secret has been deleted. List all secrets to confirm only the baseline Secret remains:

```bash
kubectl get secrets -n default | grep db

```

Output:
![List all secrets](./images/image-027.png)

Only the original `users-db-credentials` Secret exists. The sandbox-specific Secret is gone.

### Verify the Neon Branch is Gone

Navigate to your project in the Neon UI and click `Branches`. The `sandboxfeatureuserexport` branch no longer appears:
![Verify the Neon Branch is Gone](./images/image-028.png)

Let’s also confirm using the Neon CLI:

```bash
neonctl branches list --project-id crimson-scene-56898541

```

Output:
![confirm using the Neon CLI](./images/image-029.png)

The sandbox branch and all its data are permanently deleted.

## Conclusion

You have built a system that delivers true branch-based environments. Each Signadot Sandbox now gets its own forked microservice pods and its own isolated Neon database branch. The Resource Plugin handles the entire lifecycle: creating branches and Secrets on sandbox creation, cleaning them up on deletion. Test data cannot leak between sandboxes, and schema migrations in one branch cannot break tests in another.

The cost efficiency makes this practical for everyday use. Neon branches use copy-on-write storage, so you only pay for data that changes. Signadot sandboxes share baseline cluster resources. Branch creation and teardown complete in under 4 minutes. Every developer now gets an isolated app and database for every pull request.