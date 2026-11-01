{
  description = "Merge Lightroom Classic catalog backups";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs =
    { self, nixpkgs, ... }:
    let
      systems = [
        "aarch64-darwin"
        "aarch64-linux"
        "x86_64-darwin"
        "x86_64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
        in
        {
          default = pkgs.python3Packages.buildPythonApplication {
            pname = "lrc-catalog-merge";
            version = "0.1.0";
            pyproject = true;
            src = ./.;
            build-system = [ pkgs.python3Packages.setuptools ];
            nativeCheckInputs = [
              pkgs.python3Packages.pytestCheckHook
              pkgs.rocksdb.tools
            ];
            makeWrapperArgs = [ "--set-default LRC_LDB ${pkgs.rocksdb.tools}/bin/ldb" ];
            meta.mainProgram = "lrc-catalog-merge";
          };
        }
      );

      devShells = forAllSystems (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
        in
        {
          default = pkgs.mkShell {
            packages = [
              (pkgs.python3.withPackages (ps: [ ps.pytest ]))
              pkgs.rocksdb.tools
              pkgs.sqlite
            ];
          };
        }
      );
    };
}
