[← Back to the README](../README.md)

# Security model

The rules that live in the code and not in the prompt.

## Security model

- **Local only:** the server binds to `127.0.0.1`. Every request needs a token created at each start; host and origin are checked.
- **Remote access only when paired:** the device port (`JARVIS_REMOTE=1`) also binds only to `127.0.0.1` and can only be reached through `tailscale serve`. Without a paired device there is only the pairing page. A code is discarded after five failed attempts. `config/devices.json` only holds hashes of the device keys. Pairing, the device list and the confirmation interface can only be reached on the PC. Optionally `JARVIS_REMOTE_HOSTS` limits the allowed host names (default: `*.ts.net`).
- **Confirmation cards:** system actions go through Claude Code's `--permission-prompt-tool` to a card in the HUD. That includes shell commands, calendar entries and writing outside the vault. Without an answer it is declined after 75 s.
- **Protected devices:** the router never switches off the router, PC, servers or the fridge by itself. Home Assistant switches `light`, `switch`, `fan`, `media_player` and `scene`; thermostats only for the `climate.*` entities listed in `config/home.json` under `climate_groups`, with a target temperature of 5–30 °C or a supported heating mode `heat`, `off` or `auto`.
- **Questions never switch:** "Is … on?" is read, never executed.
- **No self-modification:** at runtime there are no tools for editing code.
- **Data stays local:** `.env`, `config/home.json`, `vault/`, `logs/`, `state.json` and models are in `.gitignore`. Speech recognition and the GPU voice run offline.
- **Secrets:** API keys, passwords and tokens are only ever entered in `.env` (template `.env.example`); how to change or rotate them is described in the README under [Secrets](../README.md#secrets).
