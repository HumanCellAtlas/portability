#!/bin/bash
set -ex

# Zip up the lambda deployment package
rm -f portability-service-lambda.zip
pip3 install --system -t portability-service-lambda/ requests
cd portability-service-lambda && zip -X -r ../portability-service-lambda.zip * && cd ..
aws s3 cp portability-service-lambda.zip s3://"$2"/portability-service-lambda.zip

# Create the stack
aws cloudformation create-stack --stack-name "$1" \
    --template-body file://portability_service_template.json \
    --parameters ParameterKey=LambdaCodeBucket,ParameterValue="$2" \
                 ParameterKey=LambdaCodeKey,ParameterValue=portability-service-lambda.zip \
    --capabilities CAPABILITY_NAMED_IAM
