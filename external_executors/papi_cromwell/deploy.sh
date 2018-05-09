#!/bin/bash
set -exo pipefail

rm -f wes_to_cromwell.zip
pip3 install -t wes-to-cromwell-lambda/ requests
cd wes-to-cromwell-lambda && zip -X -r ../wes_to_cromwell.zip * && cd ..
aws s3 cp wes_to_cromwell.zip s3://"$2"/wes_to_cromwell.zip
aws s3 cp workflow_execution_service.swagger.yaml s3://"$2"/workflow_execution_service.swagger.yaml

CHANGE_SET="$(python -c "import uuid; print(str(uuid.uuid4()))")"
CHANGE_SET=Z"${CHANGE_SET:1:15}"

response=$(aws cloudformation create-change-set --stack-name "$1" \
    --change-set-name "$CHANGE_SET" \
    --change-set-type CREATE \
    --template-body file://wes_to_cromwell.json \
    --parameters ParameterKey=LambdaCodeBucket,ParameterValue="$2" \
                 ParameterKey=LambdaCodeKey,ParameterValue=wes_to_cromwell.zip \
                 ParameterKey=SwaggerPath,ParameterValue=s3://"$2"/workflow_execution_service.swagger.yaml \
    --capabilities CAPABILITY_NAMED_IAM)
sleep 10
aws cloudformation execute-change-set --change-set-name $(echo "$response" | jq -r '.Id')
