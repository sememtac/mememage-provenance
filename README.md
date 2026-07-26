<p align="center">
  <img src="https://mememage.art/img/mememage-icon.png" width="112" alt="Mememage">
</p>

# Mememage — Provenance

## This is a demo app

It is a demo app built on [mememage](https://github.com/sememtac/mememage). It is an artistic take on image provenance. Its provenance features fit one artist's work.

The core is common to every app built on Mememage. A 2-pixel bar links an image to a JSON record. A hash proves that the two belong together. The core is four functions, and it does not define what a record contains.

Install it and try it on your own images. Build your own app on the core. Use this repository as an example of what to do with the data, not as a specification. You do not have to run this app to use Mememage.

## What the demo app does

It stamps images and verifies them. A **mint server** stamps and publishes an image. A web **decoder** and **validator** read the bar and check it. The bar points to a record you keep, so anyone can confirm the origin of the image from any copy.

This app also adds a snapshot of the moment it publishes an image. Each item below is an invention of this app. The format does not require any of them.

- **Celestial birth certificate** — the positions of the sun, the moon, and the planets, computed from Meeus' astronomical algorithms.
- **Machine reading** — a hardware and live-state snapshot of the publishing machine (macOS and Linux): cores, memory, load, and kernel entropy.
- **Rarity** — a score from the celestial state, the machine vitals, and raw entropy.
- **Time-locked GPS** — the coordinate, behind an RSA time-lock puzzle (sequential squaring, about 10 years).
- **Lineage** — a `parent_id` chain from every image back to a genesis.

The record also carries fields that the creator defines: a caption, a credit, a license. Enter them in the mint editor, or prefill them from the image's EXIF data (camera, lens, date).

**Build your own record.** A newsroom's record can hold a photographer and an assignment. A shop's record can hold an order number. Choose the fields that fit your work.

## What's here

- **`mememage/`** — the library. It has the `encode` / `decode` / `verify` API and the bar codec. It also has the canonical-chain implementation: the celestial certificate, rarity, Ed25519 signing, distribution channels, and the mint server.
- **`docs/`** — the web **decoder** (light) and **validator** (dark). Drop an image, read the bar, fetch the record, and verify by hash. The pages are vanilla HTML, CSS, and JS, with no build step.
- **`tools/`** — scripts to set up a public mint server on a fresh box (`bootstrap.sh`, `vps-setup.sh`) and to build the desktop app.

## Install

**Desktop app.** Download the build for your platform from [Releases](https://github.com/sememtac/mememage-provenance/releases). Each build bundles the mint server and the full web UI. You do not need Python.

- **macOS** — `Mememage.app` (double-click in Finder)
- **Windows** — `Mememage.exe`
- **Linux** — the `Mememage` binary

**From source.** Use any platform with Python 3.10 or later:

```bash
git clone https://github.com/sememtac/mememage-provenance
cd mememage-provenance
pip install ".[mint]"     # the full mememage plus Pillow, numpy, qrcode
mememage serve            # HTTPS mint server + dashboard
```

The `mememage` package here is the full library: the core API plus the canonical chain. So you do not need a separate `pip install mememage`. The mint server serves the decoder and the validator itself. You can also host `docs/` on any static surface. To set up a public server on a fresh Ubuntu or Debian box, run `tools/bootstrap.sh`. It configures TLS and nginx in one command.

## Built on

- **mememage** — the bar, record, and hash technique under everything.
- **The Python standard library** — the mint server, the chain, the RSA time-lock, and the ephemeris math run on the standard library alone.
- **Pillow** (image and bar), **cryptography** (Ed25519 and AES), and **numpy** (optional watermark) — lazy-loaded extras.
- **Vanilla HTML, CSS, and JS** — the decoder and the validator are static pages.

## Payload

A chain distributes files across its own records. Each layer in `chain.json` names a source and a cadence. The cadence is the number of records that the source spreads across before the cycle repeats. At seal time, Mememage splits the source into that many chunks, one per record. To reassemble a file, it walks the lineage, collects the chunks of one cycle, verifies them against the per-chunk hashes in the records, and rebuilds the file. When the decoder and the validator are among the distributed files, you can read a chain with no external hosting.

`chain.json` defines the layers and their sources, and points at a `payload/` directory. `ChainConfig.default()` is an example configuration. This repository provides the mechanism, not a configured chain.

## License

MIT.
