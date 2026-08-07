# etekcity-bp-daemon

A standalone Linux daemon that connects to an Etekcity Smart Blood Pressure
Monitor over Bluetooth Low Energy and logs its readings.

It's a thin wrapper around the
[`etekcity-bp-ble`](https://github.com/bonelifer/etekcity-bp-ble) library,
meant to run unattended (e.g. as a `systemd` service) near the monitor.

**Disclaimer: This is an unofficial, community-developed project. It is not
affiliated with, officially maintained by, or in any way officially
connected with Etekcity Corporation or Guangdong Transtek Medical
Electronics Co., Ltd.**

## Status

Scaffolding only — no functionality yet. Feature set is still being decided;
see [Discussions](https://github.com/bonelifer/etekcity-bp-daemon/discussions).

## Contributing

Contributions are welcome!

- **Bug reports**: [Open an issue](https://github.com/bonelifer/etekcity-bp-daemon/issues).
- **Everything else** (questions, feature requests, ideas, general discussion): [Use Discussions](https://github.com/bonelifer/etekcity-bp-daemon/discussions).
- Pull requests are welcome for bug fixes or discussed features.

## Acknowledgments

- Built on [`etekcity-bp-ble`](https://github.com/bonelifer/etekcity-bp-ble),
  which itself is based on the protocol decoding in
  [EdLeckert/ha_etekcity_blood_pressure_monitor](https://github.com/EdLeckert/ha_etekcity_blood_pressure_monitor).
- Project layout modeled on [`etekcity-scale-daemon`](https://github.com/bonelifer/etekcity-scale-daemon).
- Code review, implementation, and documentation assisted by [Claude](https://www.anthropic.com/claude).

## License

This project is licensed under the **GNU General Public License v3.0**.

See [LICENSE](LICENSE) for more information.
