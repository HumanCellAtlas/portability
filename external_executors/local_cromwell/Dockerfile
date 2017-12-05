FROM ubuntu:zesty

RUN apt-get update \
 && apt-get install -y \
      curl \
      openjdk-8-jre-headless \
      python3 \
      unzip \
      apt-transport-https \
      ca-certificates \
      software-properties-common

# Install Docker
RUN curl -fsSL https://download.docker.com/linux/ubuntu/gpg | apt-key add -
RUN add-apt-repository "deb [arch=amd64] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
 && apt-get update \
 && apt-get install -y docker-ce

# Get the cromwell jar
RUN curl -LO https://github.com/broadinstitute/cromwell/releases/download/29/cromwell-29.jar

# Copy the script and set it as the entrypoint
ADD run_portability_test.py .
ENTRYPOINT ["/run_portability_test.py"]
