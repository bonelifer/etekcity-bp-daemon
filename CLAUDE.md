# Project notes for etekcity-bp-daemon

## Related repos to watch

- **etekcity-bp-ble** -- https://github.com/home-health-hub/etekcity-bp-ble --
  this daemon's own BLE protocol library, pulled as a `git+https`
  dependency in `pyproject.toml` (not a versioned PyPI release). A fix or
  feature added there doesn't reach this daemon automatically: it needs
  `pip install --upgrade` to pick it up. See that repo's own `CLAUDE.md`
  for the upstream source (`EdLeckert/ha_etekcity_blood_pressure_monitor`)
  it tracks in turn -- a protocol fix there flows through this one too,
  eventually.

- **etekcity-scale-daemon** -- https://github.com/home-health-hub/etekcity-scale-daemon
  -- the architecture template this daemon's project layout was
  deliberately modeled on. Not a code dependency, just a design
  reference: if that project adopts a new pattern worth borrowing, it's
  worth checking.

## Verification status

See the README for current hardware-verification status against a real
Etekcity blood pressure monitor.
