self:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.lrc-catalog-merge;
  yaml = pkgs.formats.yaml { };
in
{
  options.programs.lrc-catalog-merge = {
    enable = lib.mkEnableOption "lrc-catalog-merge, a Lightroom Classic catalog backup merger";

    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      defaultText = lib.literalExpression "lrc-catalog-merge.packages.\${system}.default";
      description = "The lrc-catalog-merge package to install.";
    };

    settings = lib.mkOption {
      type = yaml.type;
      default = { };
      example = lib.literalExpression ''
        {
          sources = [ "/nas/backups/Lightroom" "/nas/backups/Lightroom-asako" ];
          outputs = [
            {
              path = "/nas/backups/Lightroom";
              exclude_folders = [ "C:/Users/asako/Pictures/" ];
            }
            { path = "/nas/backups/Lightroom-asako"; }
          ];
        }
      '';
      description = ''
        Written to {file}`$XDG_CONFIG_HOME/lrc-catalog-merge/config.yaml`.
        See the README for the keys.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    home.packages = [ cfg.package ];
    xdg.configFile."lrc-catalog-merge/config.yaml" = lib.mkIf (cfg.settings != { }) {
      source = yaml.generate "lrc-catalog-merge-config.yaml" cfg.settings;
    };
  };
}
