#!/bin/bash
set -ex

# Set up dependencies for the lambdas
# Install the DNAnexus SDK and move some things around to paths resolve
wget https://wiki.dnanexus.com/images/files/dx-toolkit-v0.250.3-ubuntu-14.04-amd64.tar.gz
tar xf dx-toolkit-v0.250.3-ubuntu-14.04-amd64.tar.gz
cp -r dx-toolkit/share/dnanexus/lib/python2.7/site-packages/* dx-wes-lambda
mkdir -p dx-wes-lambda/bin
cp dx-toolkit/bin/* dx-wes-lambda/bin
rm -r dx-toolkit-v0.250.3-ubuntu-14.04-amd64.tar.gz dx-toolkit/

# Zip up the lambda deployment package
rm -f dx_wes.zip
cd dx-wes-lambda && zip -X -r ../dx_wes.zip * && cd ..
aws s3 cp dx_wes.zip s3://"$2"/dx_wes.zip

# Create the stack
aws cloudformation create-stack --stack-name "$1" \
    --template-body file://dx_wes_template.json \
    --parameters ParameterKey=LambdaCodeBucket,ParameterValue="$2" \
                 ParameterKey=LambdaCodeKey,ParameterValue=dx_wes.zip \
    --capabilities CAPABILITY_NAMED_IAM
