# Compose apps

Container images and Foundries composeapps, built independently of the Yocto
images in the sibling `../yocto` directory.

- `matrix/`: Matrix web application for AMD64 and ARM64.
- [`led-matrix-anim/`](led-matrix-anim/README.md): UNO Q LED matrix firmware and controller
  for ARM64, with build, test, installation, and recovery instructions.

CI discovers tracked `composeapps/*/docker-compose.yml` files. To add an app,
create a directory with that file; no workflow matrix edits are needed. Directory
names must be lowercase image-compatible names and become the published
`<directory>-app` name. Nested example Compose files are not discovered.

Declare architectures with standard Compose `build.platforms`:

```yaml
services:
  web:
    image: ghcr.io/your-org/web
    build:
      context: .
      platforms: [linux/amd64, linux/arm64]
```

The build step sets `DOCKER_DEFAULT_PLATFORM=linux/arm64` for services without an
explicit platform. QEMU and Buildx setup use the runner's native architecture.
All built services in an app must support the same platforms. Each
built service should have its own `image:` repository; CI tags it with the commit
SHA. Update the image repositories when reusing this template.

The workflow discovers app directories, uses `docker compose build --print` to
produce the build configuration, and runs it with Buildx Bake. Apps build in
parallel, as do their service targets. Published image digests are passed to
composectl. Image-only services use composectl's existing resolution behavior;
apps without builds default to ARM64 for publication.

After every app publishes, one job pulls the apps into a temporary archive and
promotes it only after all downloads succeed. Whole-directory caching and the
existing update archive layout remain unchanged. All CI logic lives in
`.github/workflows/composeapps.yml`.

Inspect locally without building or publishing (requires Compose with `build --print`):

```sh
# From the repository root:
git ls-files -- ':(glob)composeapps/*/docker-compose.yml'
# From an app directory:
DOCKER_DEFAULT_PLATFORM=linux/arm64 docker compose build --print
```

Validate locally:

```sh
python3 -B -m unittest discover -s led-matrix-anim/tests -v
docker compose -f matrix/docker-compose.yml config --quiet
docker compose -f led-matrix-anim/docker-compose.yml config --quiet
```
