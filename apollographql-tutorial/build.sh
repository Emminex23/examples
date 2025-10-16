#!/bin/bash
set -e


# Build demo images
pushd ~/git/third-party/apollographql/supergraph-demo

docker build -t signadot/apollographql-demo-products:latest ./subgraphs/products
docker build -t signadot/apollographql-demo-inventory:latest ./subgraphs/inventory
docker build -t signadot/apollographql-demo-users:latest ./subgraphs/users

popd


# Build sandbox images
docker build -t signadot/apollographql-demo-users-fg:latest ./subgraphs/users
