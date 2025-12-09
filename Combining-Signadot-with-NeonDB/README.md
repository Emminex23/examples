# True “Branch-Based Environments”: Combining Signadot Sandboxes with Neon DB Branching

Ephemeral sandbox environments solve many problems for microservices teams. You can spin up an isolated copy of your service, test your changes, and tear it down. No conflicts with other developers. No waiting for a shared staging slot.

But here’s the catch: your sandbox service still connects to the same staging database as everyone else. One developer’s test writes pollute another’s queries. Schema migrations break active tests. Seed data disappears mid-run. The application layer is isolated, but the data layer is not.

This guide shows you how to fix that problem. You will combine Signadot Sandboxes with Neon’s database branching to create true full-stack isolation. Every sandbox gets its own application fork and its own database branch. When the sandbox dies, the database branch dies with it.

## What You Will Build

The end-to-end system works as follows:

1. A developer creates a Signadot Sandbox
2. A Resource Plugin automatically creates a Neon database branch and exposes the connection string as an output
3. The sandbox pod starts with a connection string pointing to the isolated branch
4. The developer runs tests against isolated data
5. The developer deletes the sandbox
6. The Resource Plugin deletes the Neon branch automatically

No shared state. No test pollution. No manual cleanup scripts.

## Prerequisites

Before you begin, ensure you have:

- A [Signadot account](https://www.signadot.com/) with a connected Kubernetes cluster
- The [Signadot operator and CLI](https://www.signadot.com/docs/getting-started/installation) installed and authenticated
- `kubectl` configured to access your cluster
- Docker installed for building container images
- Access to a container registry (Docker Hub, GitHub Container Registry, etc.)
- Node.js and npm installed (for installing the Neon CLI)

## Step 1: Set Up the Neon Database

Before configuring your microservice, you need a Neon account, project, and database with schema and seed data. The sandbox branches will copy from this parent branch.

### Create a Neon Account

If you don’t have a Neon account, sign up at [Neon.com](http://Neon.com). Neon offers a free tier that works well for development and testing.

### Install the Neon CLI

Install `neonctl` using npm:

```bash
npm install -g neonctl
```

Verify the installation:

```bash
neonctl --version
```

### Authenticate the Neon CLI

Authenticate with your Neon account:

```bash
neonctl auth
```

This command opens a browser window. Log in to your Neon account and authorize the CLI. After successful authentication, you’ll see:

![neonctl auth](./images/img-001.png)

### Generate a Neon API Key

The Resource Plugin needs an API key to create and delete branches programmatically.

1. Go to the [Neon Console](https://console.neon.tech/)
2. Click on your profile icon in the top-right corner
3. Select **Account Settings**
4. Navigate to **Personal API keys**
5. Click **Create new API key**
6. Give it a descriptive name like `signadot-resource-plugin`
7. Copy the generated key immediately (you won’t see it again)

Store this API key securely. You will use it in Step 4 when configuring the Resource Plugin.

### Create a Neon Project

Create a new project for this tutorial:

```bash
neonctl projects create --name users-demo
```

Follow the prompt.

Output:

![Create a Neon Project](./images/img-002.png)

Note the project ID (e.g., `mute-water-02173508`). You will need it throughout this tutorial.

### Verify the Project Setup

List the branches in your new project:

```bash
neonctl branches list --project-id mute-water-02173508
```

Replace `mute-water-02173508` with your actual project ID.

Expected output:

![Verify the Project Setup](./images/img-003.png)

Neon automatically creates a `main` branch when you create a project. This branch serves as the parent for all sandbox branches.

### Get the Connection String

Retrieve the connection string for the `main` branch:

```bash
neonctl connection-string main \
--project-id mute-water-02173508 \
--database-name neondb
```

Expected output:

![Get the Connection String](./images/img-004.png)

Save this connection string. You will use it to:
- Create the baseline Kubernetes Secret
- Connect via `psql` to create the schema

### Install psql (If Needed)

If you don’t have `psql` installed, install it now:

On macOS:

```bash
brew install libpq
brew link --force libpq
```

On Ubuntu/Debian:

```bash
sudo apt-get install postgresql-client
```

### Create the Schema

Connect to your Neon database using `psql`. Replace the connection string with your actual one:

```bash
psql "postgresql://neondb_owner:npg_kWDBp9eLu6nP@ep-hidden-resonance-aegr35t7.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
```

Once connected, create the `users` table:

```sql
CREATE TABLE users (
  id SERIAL PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  email VARCHAR(255) UNIQUE NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
```

Expected output:

![Create the Schema](./images/img-005.png)

### Add Seed Data

Insert sample records that sandbox branches will inherit:

```sql
INSERT INTO users (name, email) VALUES
  ('Alice Johnson', 'alice@example.com'),
  ('Bob Smith', 'bob@example.com'),
  ('Carol Williams', 'carol@example.com');
```

Expected output:

![Add Seed Data](./images/img-006.png)

### Verify the Data

Query the table to confirm the seed data:

```sql
SELECT * FROM users ORDER BY id;
```

Expected output:

![Verify the Data](./images/img-007.png)

Exit `psql`:

```bash
\q
```

Your Neon database now has a schema and seed data. Every sandbox branch will start with a copy of this state.

## Step 2: Build the Microservice

You need a microservice that reads its database connection string from an environment variable. The baseline deployment connects to the main database, while sandboxed forks connect to isolated branches through the Resource Plugin’s output.

### Create the Project Directory

Create a directory for your microservice:

```bash
mkdir users-service
cd users-service
```

### Create the Application Code

Create `app.js` with the following content:

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
  try {
    const result = await pool.query('SELECT * FROM users ORDER BY id');
    res.json(result.rows);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Create a user
app.post('/users', async (req, res) => {
  try {
    const { name, email } = req.body;
    const result = await pool.query(
      'INSERT INTO users (name, email) VALUES ($1, $2) RETURNING *',
      [name, email]
    );
    res.status(201).json(result.rows[0]);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});
```

The key line is `process.env.DATABASE_URL`:

- The service does not hardcode any database credentials
- It reads the connection string from the environment at runtime
- Kubernetes injects this value from a Secret (for baseline) or from the Resource Plugin output (for sandboxes)

### Create the Package Configuration

Create a file named `package.json` with the following content:

```json
{
  "name": "users-service",
  "version": "1.0.0",
  "description": "A simple users microservice for demonstrating Signadot sandboxes with Neon branching",
  "main": "app.js",
  "scripts": {
    "start": "node app.js"
  },
  "dependencies": {
    "express": "^4.18.2",
    "pg": "^8.11.3"
  },
  "engines": {
    "node": ">=18.0.0"
  }
}
```

Alternatively, you can generate the file using `npm` and then install the dependencies:

```bash
npm init -y
npm install express pg
```

### Create the Dockerfile

Create a `Dockerfile` to containerize the application:

```docker
FROM node:20-alpine

WORKDIR /app

# Copy package files first for better layer caching
COPY package*.json ./

# Install dependencies
RUN npm install --production

# Copy application code
COPY app.js ./

# Create non-root user for security
RUN addgroup -g 1001 -S nodejs && \
    adduser -S nodejs -u 1001

USER nodejs

EXPOSE 3000

CMD ["npm", "start"]
```

### Create a .dockerignore File

Create `.dockerignore` to exclude unnecessary files from the build context:

```
node_modules
npm-debug.log
.git
.gitignore
README.md
```

### Build the Docker Image

Build the container image:

```bash
docker build -t users-service:latest .
```

Verify the build succeeded:

```bash
docker images | grep users-service
```

Expected output:

![Build the Docker Image](./images/img-008.png)

### Test the Image Locally

You can test the image locally before pushing:

```bash
docker run -p 3000:3000 -e DATABASE_URL="your-neon-connection-string" users-service:latest
```

![Test the Image Locally](./images/img-009.png)

In another terminal:

```bash
curl http://localhost:3000/health
```

![Test the Image Locally](./images/img-010.png)

Stop the container with `Ctrl+C`.

### Push to a Container Registry

Tag and push the image to your container registry. Replace `your-registry` with your actual registry (e.g., `ghcr.io/yourusername`, `docker.io/yourusername`).

For GitHub Container Registry:

```bash
# Tag the image
docker tag users-service:latest ghcr.io/yourusername/users-service:latest

# Log in to GitHub Container Registry
echo $GITHUB_TOKEN | docker login ghcr.io -u yourusername --password-stdin

# Push the image
docker push ghcr.io/yourusername/users-service:latest
```

For Docker Hub:

```bash
# Tag the image
docker tag users-service:latest yourusername/users-service:latest

# Log in to Docker Hub
docker login

# Push the image
docker push yourusername/users-service:latest
```

Note the full image path (e.g., `ghcr.io/yourusername/users-service:latest`). You will use it in the Kubernetes Deployment.

## Step 3: Deploy to Kubernetes

Now deploy the microservice to your Kubernetes cluster with a baseline database connection.

### Verify Cluster Access

Confirm you can access your Kubernetes cluster:

```bash
kubectl cluster-info
```

Expected output:

![Verify Cluster Access](./images/img-011.png)

Verify the Signadot operator is running:

```bash
kubectl get pods -n signadot
```

![Verify the Signadot operator](./images/img-012.png)

You should see Signadot operator pods in `Running` state.

### Create the Baseline Secret

Create a file named `db-secret.yaml` containing the connection string to your main Neon branch:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: users-db-credentials
  namespace: default
type: Opaque
stringData:
  DATABASE_URL: "postgresql://neondb_owner:npg_kWDBp9eLu6nP@ep-hidden-resonance-aegr35t7.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require"
```

Replace the `DATABASE_URL` value with your actual Neon connection string from Step 1.

Apply the secret:

```bash
kubectl apply -f db-secret.yaml
```

Verify the secret was created:

```bash
kubectl get secret users-db-credentials -n default
```

Expected output:

![Create the Baseline Secret](./images/img-013.png)

### Create the Deployment

Create a file named `deployment.yaml`:

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
          imagePullPolicy: Always
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
          livenessProbe:
            httpGet:
              path: /health
              port: 3000
            initialDelaySeconds: 10
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /health
              port: 3000
            initialDelaySeconds: 5
            periodSeconds: 5
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

Replace `ghcr.io/emminex23/users-service:latest` with your actual image path from Step 2.

The important parts:

- `secretKeyRef` tells Kubernetes to read the value from a Secret
- `name: users-db-credentials` specifies which Secret to use
- `key: DATABASE_URL` specifies which key within the Secret
- `sidecar.signadot.com/inject: "true"` enables the Signadot routing sidecar

Apply the deployment:

```bash
kubectl apply -f deployment.yaml
```

### Verify the Deployment

Check that the pods are running:

```bash
kubectl get pods -l app=users-service
```

Expected output:

![Verify the Deployment](./images/img-014.png)

The `2/2` indicates that both the main container and the Signadot routing sidecar are running.

### Test the Baseline Service

Port-forward to test the service locally:

```bash
kubectl port-forward svc/users-service 3000:3000
```

In a separate terminal, test the endpoints:

```bash
# Health check
curl http://localhost:3000/health
```

Expected output:

![Health check](./images/img-015.png)

```bash
# List users
curl http://localhost:3000/users
```

Expected output:

![List users](./images/img-016.png)

You should see the three seed users from Step 1.

Stop the port-forward with `Ctrl+C`.

## Step 4: Create the Signadot Resource Plugin

A Signadot Resource Plugin is the “glue” that connects Signadot to Neon. You need a Resource Plugin because Signadot doesn’t natively know how to create Neon branches. The plugin runs custom scripts during sandbox lifecycle events:

- **On create**: Creates a Neon branch and outputs the connection string
- **On delete**: Deletes the Neon branch

The Resource Plugin framework provides [built-in support for input and output parameters](https://www.signadot.com/docs/reference/resource-plugins/overview). The connection string gets exposed as an output and referenced directly from the sandbox spec. You don’t need to manually create Kubernetes Secrets to pass the connection string between the plugin and the sandbox.

### Store the Neon API Key

The Resource Plugin runner needs access to your Neon API key. Store it in a Kubernetes Secret in the `signadot` namespace so the runner pod can authenticate with the Neon API.

Create the secret using the API key you generated in Step 1:

```bash
kubectl create secret generic neon-api-credentials \
  --namespace=signadot \
  --from-literal=NEON_API_KEY=your-neon-api-key-here
```

- `neon-api-credentials` is the name of the Secret
- `NEON_API_KEY` is the key that the runner will look for
- Replace `your-neon-api-key-here` with your actual Neon API key

Verify the secret:

```bash
kubectl get secret neon-api-credentials -n signadot
```

Expected output:

![Store the Neon API Key](./images/img-017.png)

### Define the Resource Plugin

Create a file named `neon-branch-plugin.yaml`:

```yaml
name: neon-branch
spec:
  description: Creates and deletes Neon database branches for sandbox isolation
  
  runner:
    image: node:20-alpine
    namespace: signadot
    podTemplateOverlay: |
      spec:
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
      script: |
        #!/bin/sh
        set -e

        # Install neonctl
        npm install -g neonctl

        # Generate a unique branch name using the sandbox name
        # Remove any hyphens from sandbox name for Neon branch naming
        SAFE_NAME=$(echo "${SIGNADOT_SANDBOX_NAME}" | tr -d '-')
        BRANCH_NAME="sandbox${SAFE_NAME}"

        echo "Creating Neon branch: ${BRANCH_NAME}"
        echo "Project ID: ${NEON_PROJECT_ID}"
        echo "Parent branch: ${PARENT_BRANCH}"

        # Create the branch
        neonctl branches create \
          --project-id "${NEON_PROJECT_ID}" \
          --name "${BRANCH_NAME}" \
          --parent "${PARENT_BRANCH}" \
          --output json > /tmp/branch-output.json

        cat /tmp/branch-output.json

        # Extract the branch ID
        BRANCH_ID=$(cat /tmp/branch-output.json | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
        echo "Created branch ID: ${BRANCH_ID}"

        # Get the connection string for the new branch
        CONNECTION_STRING=$(neonctl connection-string "${BRANCH_NAME}" \
          --project-id "${NEON_PROJECT_ID}" \
          --database-name "${DATABASE_NAME}")

        echo "Connection string retrieved successfully"

        # Write outputs to files for Signadot to capture
        mkdir -p /outputs
        echo -n "${BRANCH_NAME}" > /outputs/branch-name
        echo -n "${BRANCH_ID}" > /outputs/branch-id
        echo -n "${CONNECTION_STRING}" > /outputs/connection-string

        echo "Branch creation complete"

      outputs:
        - name: branch-name
          valueFromPath: /outputs/branch-name
        - name: branch-id
          valueFromPath: /outputs/branch-id
        - name: connection-string
          valueFromPath: /outputs/connection-string

  delete:
    - name: deletebranch
      inputs:
        - name: project-id
          valueFromSandbox: true
          as:
            env: NEON_PROJECT_ID
        - name: branch-name
          valueFromStep:
            name: createbranch
            output: branch-name
          as:
            env: BRANCH_NAME
      script: |
        #!/bin/sh
        set -e

        # Install neonctl
        npm install -g neonctl

        echo "Deleting Neon branch: ${BRANCH_NAME}"
        neonctl branches delete "${BRANCH_NAME}" \
          --project-id "${NEON_PROJECT_ID}"

        echo "Cleanup complete"
```

The plugin has three main sections:

1. **runner**: Specifies the container image (`node:20-alpine`) which provides a lightweight Node.js environment. The script installs `neonctl` at runtime. The Neon API key is injected via `podTemplateOverlay`. The runner executes in the `signadot` namespace where the API key secret exists.
2. **create**: Defines the workflow that runs when a sandbox starts. The step:
- Accepts three inputs from the sandbox specification: `project-id`, `parent-branch`, and `database-name`
- Installs `neonctl`
- Sanitizes the sandbox name by removing hyphens (Neon branch names work best with alphanumeric characters)
- Creates a branch with a name derived from `SIGNADOT_SANDBOX_NAME` (a built-in variable)
- Retrieves the connection string for the new branch
- Writes outputs including the connection string for the sandbox to consume directly
1. **delete**: Defines the workflow that runs when a sandbox is deleted. The step:
- Installs `neonctl` (the delete step runs in a fresh container)
- Reads the branch name from the create step’s outputs (using `valueFromStep`)
- Deletes the Neon branch

### Install the Resource Plugin

Apply the plugin to your Signadot organization:

```bash
signadot resourceplugin apply -f neon-branch-plugin.yaml
```

Verify the plugin exists:

```bash
signadot resourceplugin list
```

Expected output:

![Install the Resource Plugin](./images/img-018.png)

## Step 5: Configure the Signadot Sandbox

Now you need a sandbox specification that ties everything together. The sandbox specification tells Signadot:

- Which Resource Plugin to invoke and with what parameters
- Which Deployment to fork
- How to inject the branch’s connection string into the forked pod

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
        database-name: "neondb"

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
        target: http://users-service.default.svc:3000
```

From the above:

1. **resources**: Tells Signadot to invoke the `neon-branch` plugin when creating this sandbox. The `params` block passes values to the plugin’s inputs:
- `project-id`: Your Neon project ID (passed at apply time)
- `parent-branch`: The branch to copy from (`main`)
- `database-name`: The database name within the project (`neondb`)
1. **forks**: Creates a copy of the `users-service` Deployment. The `customizations.env` block overrides the `DATABASE_URL` environment variable. The `valueFrom.resource` field references the Resource Plugin’s output directly:
- `name: usersDb` specifies which resource to read from
- `outputKey: createbranch.connection-string` follows the format `<step-name>.<output-name>`

The forked pod receives the connection string directly from the Resource Plugin output—no intermediate Kubernetes Secret required.

1. **defaultRouteGroup**: Creates a preview URL that routes traffic to your sandboxed service.

## Step 6: Test the Complete Workflow

Now walk through the full workflow to verify everything works.

### Create the Sandbox

Find your Signadot cluster name first:

```bash
signadot cluster list
```

Expected output:

![Create the Sandbox](./images/img-019.png)

Apply the sandbox specification with your specific values:

```bash
signadot sandbox apply -f users-sandbox.yaml \
  --set sandbox-name=featureuserexport \
  --set cluster=test-cluster \
  --set neon-project-id=mute-water-02173508
```

- `-set sandbox-name=featureuserexport` names your sandbox
- `-set cluster=test-cluster` specifies which Kubernetes cluster to use (use your actual cluster name)
- `-set neon-project-id=mute-water-02173508` passes your Neon project ID to the Resource Plugin (use your actual Neon project ID from Step 1)

Expected output:

![Create the Sandbox](./images/img-020.png)

### View the Resource Plugin Logs

While the sandbox is being created, Signadot runs the Resource Plugin’s create workflow. You can observe this in real-time through the Signadot dashboard:

1. Go to the [Signadot Dashboard](https://app.signadot.com/)
2. Navigate to **Sandboxes**
3. Click on **featureuserexport**
4. Click on the **Resources** tab
5. View the logs for the `usersDb` resource

You should see output confirming connection string retrieval and branch creation:

![View the Resource Plugin Logs](./images/img-021.png)

### Verify the Neon Branch Was Created

Open the [Neon Console](https://console.neon.tech/) and navigate to your project. Click on **Branches** to confirm the new branch exists:

![Verify the Neon Branch Was Created](./images/img-022.png)

You can also verify using the command line:

```bash
neonctl branches list --project-id mute-water-02173508
```

Replace `mute-water-02173508` with your actual Project ID.

Expected output:

![Verify the Neon Branch Was Created](./images/img-023.png)

- The `main` branch is your original database
- The `sandboxfeatureuserexport` branch is the copy created for your sandbox

### Query Existing Data Through the Sandbox

The sandbox branch inherits all data from its parent. Verify this by querying the users.

Get your Signadot API key from the [Signadot Dashboard](https://app.signadot.com/) under **Settings > API Keys**.

Query the sandbox endpoint:

```bash
curl -H "signadot-api-key: your-signadot-api-key" \
  "https://users-api--featureuserexport.preview.signadot.com/users"
```

Replace `your-signadot-api-key` with your actual API key.

Expected output:

![Query Existing Data Through the Sandbox](./images/img-024.png)

The three users from the main branch appear in the sandbox. The copy-on-write branch contains identical data.

### Write Test Data to the Sandbox

Create a new user that exists only in the sandbox branch:

```bash
curl -X POST \
  -H "signadot-api-key: your-signadot-api-key" \
  -H "Content-Type: application/json" \
  -d '{"name": "Sandbox User", "email": "sandbox@test.example"}' \
  "https://users-api--featureuserexport.preview.signadot.com/users"
```

Expected output:

![Write Test Data to the Sandbox](./images/img-025.png)

Query the sandbox again:

```bash
curl -H "signadot-api-key: your-signadot-api-key" \
  "https://users-api--featureuserexport.preview.signadot.com/users"
```

Expected output:

![Write Test Data to the Sandbox](./images/img-026.png)

The sandbox now contains four users.

### Query the Sandbox Branch Directly

Connect directly to the Neon branch using `psql` to confirm the data exists at the database level.

Get the sandbox branch connection string:

```bash
neonctl connection-string sandboxfeatureuserexport \
  --project-id mute-water-02173508 \
  --database-name neondb
```

Expected output:

![Query the Sandbox Branch Directly](./images/img-027.png)

Connect with psql:

```bash
psql "postgresql://neondb_owner:npg_kWDBp9eLu6nP@ep-proud-butterfly-aew3i69b.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
```

Query all users:

```sql
SELECT * FROM users ORDER BY id;
```

Expected output:

![Query the Sandbox Branch Directly](./images/img-028.png)

The sandbox branch contains all four users, including the test user.
Exit psql:

```bash
\q
```

### Verify the Main Branch is Unaffected

Connect directly to the main branch and confirm it does NOT contain the sandbox test user.

Get the main branch connection string:

```bash
neonctl connection-string main \
--project-id mute-water-02173508 \
--database-name neondb
```

Expected output:

![Verify the Main Branch is Unaffected](./images/img-029.png)

Connect with psql:

```bash
psql "postgresql://neondb_owner:npg_kWDBp9eLu6nP@ep-hidden-resonance-aegr35t7.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
```

Query all users:

```sql
SELECT * FROM users ORDER BY id;
```

Expected output:

![Query all users](./images/img-030.png)

The main branch contains only three users. The test user does not exist here.

Search explicitly for the sandbox user:

```sql
SELECT * FROM users WHERE email = 'sandbox@test.example';
```

Expected output:

![Search explicitly for the sandbox user](./images/img-031.png)

Zero rows. The main branch remains completely clean.

Exit psql:

```bash
\q
```

Data isolation is confirmed. The sandbox branch and main branch operate independently. Changes in one do not affect the other.

### Delete the Sandbox

When testing is complete, delete the sandbox:

```bash
signadot sandbox delete featureuserexport
```

Expected output:

![Delete the Sandbox](./images/img-032.png)

Signadot runs the Resource Plugin’s delete workflow automatically. The workflow deletes the Neon branch.

### Verify the Sandbox Pod is Gone

Confirm the forked pod no longer exists:

```bash
kubectl get pods -l signadot.com/sandbox=featureuserexport
```

Expected output:

![Verify the Sandbox Pod is Gone](./images/img-033.png)

### Verify the Neon Branch is Gone

Navigate to your project in the [Neon Console](https://console.neon.tech/) and click **Branches**. The `sandboxfeatureuserexport` branch no longer appears:

![Verify the Neon Branch is Gone](./images/img-034.png)

Confirm using the Neon CLI:

```bash
neonctl branches list --project-id mute-water-02173508
```

Expected output:

![Confirm using the Neon CLI](./images/img-035.png)

The sandbox branch and all its data are permanently deleted.

## Conclusion

You have built a system that delivers true branch-based environments. Each Signadot Sandbox now gets its own forked microservice pods and its own isolated Neon database branch. The Resource Plugin handles the entire lifecycle: creating branches on sandbox creation, exposing connection strings through built-in outputs, and cleaning them up on deletion. Test data cannot leak between sandboxes, and schema migrations in one branch cannot break tests in another.

The cost efficiency makes this practical for everyday use. Neon branches use copy-on-write storage, so you only pay for data that changes. Signadot sandboxes share baseline cluster resources. Branch creation and teardown complete in few minutes. Every developer now gets an isolated app and database for every pull request.