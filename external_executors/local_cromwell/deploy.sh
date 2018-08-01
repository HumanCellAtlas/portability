#!/bin/bash
set -exo pipefail

echo "Creating stack named $1"
echo "Uploading to bucket $2"

AWS="aws"

rm -f local_cromwell.zip
pip3 install --system -t local-cromwell-lambda/ requests requests-toolbelt
cd local-cromwell-lambda && zip -X -r ../local_cromwell.zip * && cd ..
$AWS s3 cp local_cromwell.zip s3://"$2"/local_cromwell.zip
$AWS s3 cp workflow_execution_service.swagger.yaml s3://"$2"/workflow_execution_service.swagger.yaml

CHANGE_SET="$(python -c "import uuid; print(str(uuid.uuid4()))")"
CHANGE_SET=Z"${CHANGE_SET:1:15}"

response=$($AWS cloudformation create-change-set --stack-name "$1" \
    --change-set-name "$CHANGE_SET" \
    --change-set-type CREATE \
    --template-body file://local_cromwell_template.yaml \
    --parameters ParameterKey=LambdaCodeBucket,ParameterValue="$2" \
                 ParameterKey=LambdaCodeKey,ParameterValue=local_cromwell.zip \
                 ParameterKey=InstanceType,ParameterValue=r4.large \
                 ParameterKey=VolumeSize,ParameterValue=12 \
                 ParameterKey=SwaggerPath,ParameterValue=s3://"$2"/workflow_execution_service.swagger.yaml \
    --capabilities CAPABILITY_NAMED_IAM)
sleep 20
$AWS cloudformation execute-change-set --change-set-name $(echo "$response" | jq -r '.Id')
