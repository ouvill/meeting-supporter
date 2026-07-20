{
  description = "Meeting Supporter — Japanese desktop AI meeting assistant dev environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfree = true;
        };

        # Expose libstdc++ for Python native extensions (numpy, faster-whisper, etc.)
        libPath = pkgs.lib.makeLibraryPath [
          pkgs.stdenv.cc.cc.lib
          pkgs.zlib
          pkgs.openssl
          pkgs.libgcc.lib
          pkgs.libpulseaudio
          pkgs.fontconfig
          pkgs.libglvnd
          pkgs.mesa
        ];
      in
      {
        devShells.default = pkgs.mkShell {
          name = "meeting-supporter";

          buildInputs = with pkgs; [
            # Python toolchain
            python313
            uv

            # Node.js / frontend
            nodejs_22

            # Rust (Tauri)
            rustup
            cargo

            # C/C++ toolchain for native extensions
            gcc
            gnumake
            pkg-config

            # Libraries required by Python native packages
            zlib
            openssl
            libgcc.lib
            libpulseaudio
            pulseaudio

            # Tauri desktop dependencies (Linux)
            glib
            gtk3
            libsoup_3
            webkitgtk_4_1
            libappindicator-gtk3
            librsvg
            cairo
            pango
            harfbuzz
            atk
            gdk-pixbuf
            gobject-introspection
            libdrm
            mesa
            fontconfig
            libglvnd
            vulkan-loader
            wayland
            libxkbcommon
            libx11
            libxcomposite
            libxdamage
            libxext
            libxfixes
            libxrandr
            libxcb
            libxkbfile
            libxtst
          ];

          shellHook = ''
            # Ensure stable Rust toolchain is active
            rustup default stable >/dev/null 2>&1 || true

            echo "🎤 Meeting Supporter dev shell"
            echo "   Python: $(python3 --version)"
            echo "   Node:   $(node --version)"
            echo "   uv:     $(uv --version)"
            echo "   Rust:   $(rustc --version 2>/dev/null || echo 'not available')"
            echo ""

            # Make libstdc++ discoverable for Python wheels with native extensions
            export LD_LIBRARY_PATH="${libPath}:$LD_LIBRARY_PATH"

            # Tauri expects pkg-config paths
            export PKG_CONFIG_PATH="${pkgs.lib.makeSearchPath "lib/pkgconfig" [
              pkgs.glib
              pkgs.gtk3
              pkgs.libsoup_3
              pkgs.webkitgtk_4_1
              pkgs.libappindicator-gtk3
              pkgs.librsvg
              pkgs.cairo
              pkgs.pango
              pkgs.harfbuzz
              pkgs.atk
              pkgs.gdk-pixbuf
              pkgs.libdrm
              pkgs.mesa
              pkgs.vulkan-loader
              pkgs.wayland
              pkgs.libxkbcommon
              pkgs.libx11
              pkgs.libxcomposite
              pkgs.libxdamage
              pkgs.libxext
              pkgs.libxfixes
              pkgs.libxrandr
              pkgs.libxcb
              pkgs.libxkbfile
              pkgs.libxtst
            ]}:$PKG_CONFIG_PATH"

            # Allow uv to find the right Python
            export UV_PYTHON="$(command -v python3)"

            # Node / npm path (usually already in PATH via buildInputs)
            if [ -d "$PWD/node_modules/.bin" ]; then
              export PATH="$PWD/node_modules/.bin:$PATH"
            fi
          '';
        };
      });
}
