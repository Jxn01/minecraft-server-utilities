# Minimal mcsu image: a JRE plus the mcsu CLI.
#
#   docker build -t mcsu -f deploy/Dockerfile .
#   docker run -it --rm -p 25565:25565 -v "$PWD/survival:/data" mcsu
#
# /data must contain an mcsu.toml (and, after `mcsu install`, a server jar).
# Eclipse Temurin ships a modern JRE; pick the Java version your Minecraft
# release requires (21 for 1.20.5+, 17 for 1.18–1.20.4, etc.).
FROM eclipse-temurin:21-jre

# Install Python (mcsu requires 3.11+; this base is Ubuntu 24.04 -> 3.12).
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/mcsu
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip3 install --no-cache-dir --break-system-packages .

# The server lives in a mounted volume.
VOLUME ["/data"]
WORKDIR /data
EXPOSE 25565

ENTRYPOINT ["mcsu"]
CMD ["run", "--no-console"]
