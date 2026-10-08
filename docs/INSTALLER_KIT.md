# Vibecrafted Installer Kit

This kit installs a signed Vibecrafted Runtime Pack without a source checkout.
It contains the installer, its shell libraries, and the trusted release public
key. The Runtime Pack is supplied separately and includes Python and the actual
installation engine.

On macOS, the existing installer requires a usable stable Xcode developer
directory. On macOS and Linux, Bash, tar, OpenSSL, and either `shasum` or
`sha256sum` must be available. Use a pack for your operating system and CPU.

1. Extract `Vibecrafted_InstallerKit.tar.gz` and open a terminal in
   `Vibecrafted_InstallerKit`.
2. Download the matching Runtime Pack and both sidecars (`.tar.gz.sha256` and
   `.tar.gz.sig`) from the same trusted release. Keep all three together.
3. Run the command below, replacing the example path with your downloaded pack:

   ```sh
   bash ./install-runtime-pack.sh --pack /absolute/path/to/Vibecrafted_RuntimePack_VERSION-PLATFORM.tar.gz
   ```

The installer verifies the checksum, signature, and pack contents before
installation. No repository, system Python, or public-key environment variable
is needed. Keep the kit's files together. The adjacent public key takes priority;
only when it is absent does the installer consult
`VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY`, then the source checkout's trust directory.

To verify a pack without installing it, add `--verify-only`. To uninstall an
installed runtime, run `bash ./install-runtime-pack.sh --uninstall`.

Maintainers build this archive with `make installer-kit`. The default output is
`dist/Vibecrafted_InstallerKit.tar.gz`; set `INSTALLER_KIT_OUTPUT=/path/kit.tar.gz`
to choose another destination. This packages installer files only; it does not
build, sign, notarize, or publish a Runtime Pack or app.
