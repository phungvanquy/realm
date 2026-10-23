# Run Realm in a container

The public images below support both `linux/amd64` and `linux/arm64`. Docker selects the architecture automatically.

| Image | Features |
| --- | --- |
| `ghcr.io/phungvanquy/realm:master` | Full build, including TLS/WebSocket transports, load balancing, and PROXY protocol |
| `ghcr.io/phungvanquy/realm-slim:master` | Smaller build for basic TCP/UDP forwarding |

The current image tag is `:master`. Install Docker Engine and the Compose plugin using the [Ubuntu installation guide](https://docs.docker.com/engine/install/ubuntu/) if needed.

## Quick start

Replace `192.168.1.100:443` with the service you want to reach:

```bash
sudo docker run -d --name realm --restart unless-stopped \
  -p 5000:5000/tcp \
  ghcr.io/phungvanquy/realm:master \
  -l 0.0.0.0:5000 -r 192.168.1.100:443
```

Connect to port `5000` on the Docker host to reach the destination. Check the container and follow its logs:

```bash
sudo docker ps --filter name=realm
sudo docker logs --tail 50 -f realm
```

Press **Ctrl+C** to stop following logs; the container keeps running. `--restart unless-stopped` starts it again after a host reboot unless you have explicitly stopped it.

For local access only, change the port mapping to `-p 127.0.0.1:5000:5000/tcp`.

Use one setup at a time on port `5000`. Before switching to the alternatives below, stop and remove the quick-start container:

```bash
sudo docker stop realm
sudo docker rm realm
```

### TCP and UDP

To forward both protocols, publish both ports and enable UDP with `-u`:

```bash
sudo docker run -d --name realm --restart unless-stopped \
  -p 5000:5000/tcp -p 5000:5000/udp \
  ghcr.io/phungvanquy/realm:master \
  -u -l 0.0.0.0:5000 -r 192.168.1.100:443
```

For UDP only, add `-t` and omit the TCP port mapping.

### Use a configuration file

Create and edit `/etc/realm/config.toml` using the [configuration example in the README](readme.md#run-your-first-relay), then mount it read-only:

```bash
sudo docker run -d --name realm --restart unless-stopped \
  -p 5000:5000/tcp \
  --mount type=bind,src=/etc/realm/config.toml,dst=/etc/realm/config.toml,readonly \
  ghcr.io/phungvanquy/realm:master \
  -c /etc/realm/config.toml
```

Publish every port listed in the configuration. For UDP, set `use_udp = true` and also publish the corresponding UDP port. Restart the container after configuration changes:

```bash
sudo docker restart realm
```

### Forward to a service on the Ubuntu host

Inside a container, `127.0.0.1` refers to that container. To reach a service listening on the host loopback address, use host networking on Ubuntu:

```bash
sudo docker run -d --name realm --restart unless-stopped \
  --network host \
  ghcr.io/phungvanquy/realm:master \
  -l 0.0.0.0:5000 -r 127.0.0.1:443
```

With host networking, Realm listens directly on the host, so no `-p` mappings are needed.

## Docker Compose

Save this as `compose.yaml` and replace the destination address:

```yaml
services:
  realm:
    image: ghcr.io/phungvanquy/realm:master
    restart: unless-stopped
    ports:
      - "5000:5000/tcp"
    command: ["-l", "0.0.0.0:5000", "-r", "192.168.1.100:443"]
```

Run these commands from the directory containing `compose.yaml`:

```bash
sudo docker compose up -d
sudo docker compose logs --tail 50 -f realm
```

For TCP and UDP, add `"5000:5000/udp"` to `ports` and `"-u"` to `command`.

Update to the current image:

```bash
sudo docker compose pull
sudo docker compose up -d
```

Stop and remove the Compose deployment:

```bash
sudo docker compose down
```

## Update a container started with docker run

Pull the image, stop and remove the old container, then repeat your original `docker run` command with the same destination, ports, and mounts:

```bash
sudo docker pull ghcr.io/phungvanquy/realm:master
sudo docker stop realm
sudo docker rm realm
```

Use `ghcr.io/phungvanquy/realm-slim:master` in both the pull and run commands if you installed the slim image. A restart alone does not replace the image used by a container.

For existing deployments using `realm2` or `realm2-slim`, change the image name to `realm` or `realm-slim` and recreate the container. For Compose, update the `image:` field before running the update commands above.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `manifest unknown` | Use `ghcr.io/phungvanquy/realm:master` or `ghcr.io/phungvanquy/realm-slim:master`. |
| `denied` when pulling | These images are public. For a private copy, [sign in to GHCR](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry#authenticating-to-the-container-registry) with an account that can read the package and a classic token with `read:packages`. |
| Container name or port already in use | Stop the earlier container or choose a different name and host port. |
| Container exits immediately | Read `sudo docker logs realm` and check the arguments or mounted configuration. |
| TCP works but UDP does not | Enable `-u` or `use_udp = true`, publish the UDP port, and allow UDP in network firewall rules. |
| Cannot reach the destination | Check the address from inside the container network; use the host-networking example for a service on the host loopback address. |

## Kubernetes

<details>
<summary>Example Deployment and LoadBalancer Service</summary>

Replace `192.168.1.100:443` with a destination reachable from your cluster, save as `realm.yaml`, and apply with `kubectl apply -f realm.yaml`. A `LoadBalancer` service requires load-balancer support in the cluster.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: realm
spec:
  replicas: 1
  selector:
    matchLabels:
      app: realm
  template:
    metadata:
      labels:
        app: realm
    spec:
      containers:
        - name: realm
          image: ghcr.io/phungvanquy/realm:master
          imagePullPolicy: Always
          args: ["-l", "0.0.0.0:5000", "-r", "192.168.1.100:443"]
          ports:
            - name: tcp
              containerPort: 5000
              protocol: TCP
          resources:
            requests:
              memory: "64Mi"
              cpu: "250m"
            limits:
              memory: "128Mi"
              cpu: "500m"
---
apiVersion: v1
kind: Service
metadata:
  name: realm
spec:
  type: LoadBalancer
  selector:
    app: realm
  ports:
    - name: tcp
      port: 5000
      targetPort: tcp
      protocol: TCP
```

</details>
