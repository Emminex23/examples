#!/bin/bash
set -e

cd ~/git/thrid-party/apollographql/supergraph-demo

# Create the products subgraph
rover subgraph publish "${APOLLO_GRAPH_NAME}@${APOLLO_BASELINE_VARIANT}" \
  --schema ./subgraphs/products/products.graphql \
  --name products \
  --routing-url http://products.apollographql-demo.svc:4000/graphql


# Create the inventory subgraph
rover subgraph publish "${APOLLO_GRAPH_NAME}@${APOLLO_BASELINE_VARIANT}" \
  --schema ./subgraphs/inventory/inventory.graphql \
  --name inventory \
  --routing-url http://inventory.apollographql-demo.svc:4000/graphql


# Create the users subgraph
rover subgraph publish "${APOLLO_GRAPH_NAME}@${APOLLO_BASELINE_VARIANT}" \
  --schema ./subgraphs/users/users.graphql \
  --name users \
  --routing-url http://users.apollographql-demo.svc:4000/graphql

