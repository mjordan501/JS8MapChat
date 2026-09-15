# JS8MapChat

JS8MapChat maps JS8Call callsigns in real time, with an optional fast chat window for communicating with each selected station.

**Version 1.75 — Windows and Linux**

## Videos

- [Overview of JS8Map](https://youtu.be/p99_L9B1YA8)
- [Overview of the JS8FastChat App](https://youtu.be/yJJw8RieM4U)

## Related

**[FCC Lookup](https://github.com/mjordan501/FCC_Lookup)** — offline callsign lookup for U.S. FCC and Canadian amateur licenses. Builds the same `ham.db` database JS8Map uses.

## What is in the suite

JS8MapChat is two programs that work together. Either one runs on its own; run both and a button in each window brings the other to the front.

### JS8Map

Plots the stations JS8Call is hearing onto a live map. Markers are placed by looking the callsign up in the FCC and Canadian license databases, which JS8Map builds on your own machine.

- Filters for all stations, stations you hear, mutual contacts, and stations reaching you via heartbeat
- Relay path display, showing outbound and return legs as they complete
- Offline basemap, so the map draws with no internet connection
- Light and dark themes

### JS8FastChat

A compact chat window for working a selected station without going back to the JS8Call interface.

- Message store and inbox
- Watch words
- Follows other traffic on frequency

## Requirements

- **JS8Call**, running, with its TCP API enabled
- **TCP Max Connections set to 5** in JS8Call
- Windows 10 or later, or a current Linux desktop

JS8MapChat talks to JS8Call over its network API. It does not modify JS8Call.

## Installing

Installers for Windows and packages for Linux are on the [Releases](../../releases) page. Illustrated guides for both apps are in the [docs](docs) folder.

On Linux, install from inside the unpacked package folder.

## Keyboard shortcuts (JS8Map)

| Key | Action |
|-----|--------|
| `F` | Fit all stations |
| `R` | Pull fresh data from JS8Call and rebuild |
| `/` | Jump to the Find callsign box |
| `L` | Labels on and off |
| `D` | Dark and light theme |
| `1` | Show all stations |
| `2` | Show stations you hear |
| `3` | Show mutual contacts |
| `4` | Show stations reaching you via heartbeat |
| `+` | Zoom in |
| `-` | Zoom out |
| Arrow keys | Pan |
| Shift + drag | Zoom to a box |
| `Esc` | Close a popup, or cancel a relay chain |
| `Ctrl` + `L` | Open the API log |
| `Ctrl` + `R` | Rebuild callsign coordinates |

Every key raises a short on-screen confirmation.

## Building from source

Both apps are Python. Windows builds use PyInstaller driven by the `.bat` files in each app folder, with Inno Setup for the installers. Linux builds run in a container and produce a `.tar.gz` package.

`shared_resolver.py` exists separately inside each app and the two copies must stay byte-identical. `check_twins.bat` verifies this.

Callsign databases are not included. They are built on first run from public license data.

## Credits

**JS8Call** by KN4CRD and contributors. JS8MapChat is an independent companion and is not affiliated with or endorsed by the JS8Call project.

**Leaflet** — map display library, © Vladimir Agafonkin and contributors, BSD 2-Clause licensed.

**Map data** — © OpenStreetMap contributors, available under the Open Database License. Basemap styling © CARTO.

**FCC license database** — United States government work, public domain. **Canadian amateur callsign data** — open data published by ISED Canada.

## License

Copyright (C) 2026 MJ Jordan (KW3KW)

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

The full license text is in [LICENSE](LICENSE).

## Reporting problems

Open an issue on the [Issues](../../issues) tab. Please include: which app, Windows or Linux, your JS8Call version, and what you expected to happen.
