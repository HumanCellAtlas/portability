rm -f local_cromwell.zip
zip local_cromwell.zip local_cromwell.py
aws s3 cp local_cromwell.zip s3://"$2"/local_cromwell.zip

aws cloudformation create-stack --stack-name "$1" \
    --template-body file://local_cromwell_template.json \
    --parameters ParameterKey=LambdaCodeBucket,ParameterValue="$2" \
                 ParameterKey=LambdaCodeKey,ParameterValue=local_cromwell.zip \
                 ParameterKey=InstanceType,ParameterValue=r4.xlarge \
                 ParameterKey=VolumeSize,ParameterValue=71 \
    --capabilities CAPABILITY_NAMED_IAM
