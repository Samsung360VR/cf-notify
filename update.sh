#!/usr/bin/env bash

STACK_NAME=cf-notify
LAMBDA_FUNC=`aws cloudformation describe-stack-resources --stack-name $STACK_NAME --logical-resource-id CFNotifyFunction|jq .StackResources[0].PhysicalResourceId -c| tr -d '"'`
zip cf-notify.zip lambda_notify.py slack.py
aws lambda update-function-code --function-name $LAMBDA_FUNC --zip-file fileb://cf-notify.zip
