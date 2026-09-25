**NOTE: this project is still a work in progress :)**

<h1 align="center">Featherframe</h1>

<p align="center">
  <strong>An e-paper frame that shows the birds in your backyard as Audubon prints.</strong>
</p>

<p align="center">
  <a href="https://github.com/wr/featherframe/actions/workflows/ci.yml"><img src="https://github.com/wr/featherframe/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
</p>

<p align="center">
  <a href="#how-it-works">How it works</a> ⬪
  <a href="#shopping-list">Shopping list</a> ⬪
  <a href="#get-started">Get started</a> ⬪
  <a href="https://github.com/wr/featherframe/wiki">Wiki</a> ⬪
  <a href="#license">License</a>
</p>

<center><img width="600" alt="featherframe" src="https://github.com/user-attachments/assets/22e61eee-6bd7-49b2-96bb-d9fbfed88f1a" /></center>

## How it works

A bird detector identifies the birds in your backyard by their calls. Featherframe finds the matching illustration in Audubon's [*The Birds of America*](https://www.audubon.org/art/birds-of-america) and shows it on an e-paper frame, in grayscale or color.

```
 BirdNET-Pi, BirdNET-Go   ──▶  Featherframe server  ──▶  E-paper frame
 or BirdWeather                (chooses and draws        (shows the picture)
 (identifies the bird)          the illustration)
```

It works with [BirdNET-Pi](https://github.com/Nachtzuster/BirdNET-Pi), [BirdNET-Go](https://github.com/tphakala/birdnet-go), or a [BirdWeather](https://www.birdweather.com) station.

- **Audubon's illustrations.** Every species Audubon painted gets his illustration, matted like a print.
- **Birds Audubon never painted.** These get a name card: the species' name set in type. With an OpenAI key, Featherframe can draw a new illustration in Audubon's style instead.
- **A daily collage.** One sheet shows every species heard that day.
- **Other screens.** A TRMNL, a Kobo, a Kindle, or a tablet can also show the pictures.
- **No wrong birds.** If Featherframe isn't sure of a match, it shows the name instead of a guess.

<center><img width="600" alt="IMG_1899" src="https://github.com/user-attachments/assets/95d46050-47f6-4af5-8e1a-6dfe1475b7b2" /></center>

## Shopping list

- **[Seeed XIAO ePaper DIY Kit EE03](https://www.seeedstudio.com/)**: 10.3", grayscale. Or the **EE02** kit: 13.3", color.
- **[A frame](https://amzn.to/3V1nJFo)**
- **A USB-C power supply**

You don't need the kit if you have a TRMNL, an e-reader, or a tablet. See [Other screens](https://github.com/wr/featherframe/wiki/Other-screens).

## Get started

### 1. Set up the server

Choose one:

- **Hosted.** Join the waitlist at [featherframe.app](https://featherframe.app). You'll get an invitation by email. There's nothing to install.
- **On your BirdNET device.** Run these commands on the device that runs BirdNET-Pi or BirdNET-Go:

  ```bash
  git clone https://github.com/wr/featherframe ~/featherframe
  cd ~/featherframe/server
  ./install.sh
  ```

  When it finishes, it prints the address of your Featherframe webapp, for example `http://birdnet.local:8181`. Open it and [connect your detection source](https://github.com/wr/featherframe/wiki/Detection-sources).

### 2. Install the firmware

1. Connect the frame to your computer with a USB-C cable.
2. Open your Featherframe webapp in Chrome or Edge.
3. In the **Frames** section, click **⋯**, then **USB firmware update**. If your server is on your BirdNET device, this opens the installer at [wr.github.io/featherframe](https://wr.github.io/featherframe/).
4. Click **Connect**, then follow the steps. You'll enter your Wi-Fi details at the end.

### 3. Add the frame

- **Hosted:** The frame shows a six-letter code. In your Featherframe webapp, click **⋯**, then **Pair a frame**, and enter the code.
- **On your BirdNET device:** In your Featherframe webapp, click **Add** next to the new frame.

The frame shows a picture the next time your detector identifies a bird.

## Learn more

See the [wiki](https://github.com/wr/featherframe/wiki) for:

- [Installing the server](https://github.com/wr/featherframe/wiki/Install-the-server)
- [Building](https://github.com/wr/featherframe/wiki/Build-the-frame), [flashing](https://github.com/wr/featherframe/wiki/Flash-the-frame), and [adding](https://github.com/wr/featherframe/wiki/Add-a-frame) a frame
- [Settings](https://github.com/wr/featherframe/wiki/Settings)
- [Running on a battery](https://github.com/wr/featherframe/wiki/Battery)
- [Other screens](https://github.com/wr/featherframe/wiki/Other-screens)
- [AI illustrations](https://github.com/wr/featherframe/wiki/AI-illustrations)
- [Troubleshooting](https://github.com/wr/featherframe/wiki/Troubleshooting)
- [Previewing without hardware](https://github.com/wr/featherframe/wiki/Development) and [porting to another panel](https://github.com/wr/featherframe/wiki/Porting-to-another-panel)

## Credits

Plates: John James Audubon, *The Birds of America*, public domain, via [nathanbuchar/audubon-bird-plates](https://github.com/nathanbuchar/audubon-bird-plates). Courtesy of the John James Audubon Center at Mill Grove, Montgomery County Audubon Collection, and Zebra Publishing. Fonts and libraries are listed in [THIRD_PARTY.md](THIRD_PARTY.md).

## Donate

Featherframe is free and open source. Donations pay for its development and support: [Buy me a coffee](https://www.buymeacoffee.com/wellsworkshop).

## License

[Apache License 2.0](LICENSE). The bundled fonts use the SIL Open Font License. Audubon's plates are in the public domain. See [THIRD_PARTY.md](THIRD_PARTY.md).
