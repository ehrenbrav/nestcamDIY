# NestCamDIY

NestCamDIY is a Raspberry Pi-based video camera that can be installed in a birdhouse, squirrel house, or other animal dwelling. Depending on how you build it, it can be powered by a wired power supply, a battery, or solar power. It works in both ambient light and complete darkness and streams a video feed to an address on your network, so you can view it in any browser. This allows live viewing from your phone, a computer, or even a dedicated video monitor.

The interior of the box is illuminated by infrared lights, which are invisible to both birds and humans but still allow the image to show up clearly on video, although with distorted colors. It also incorporates a motion sensor that starts recording video whenever motion is detected. Recordings can then be downloaded and viewed via the web page.

The simplest power setup is just to plug it in. You can run an outdoor extension cord to the birdhouse, plug in an outdoor USB charger, and connect this to the device. Alternatively, you can use either a battery or a solar setup. Both involve using an uninterruptible power supply to power the device while you are swapping out the battery or when it is dark out.

You can leave a weatherproof battery somewhere convenient, such as at the base of the tree, and run a USB charging cord from it to the device. For solar, you will need to experiment to find a suitable solar panel size and location. In sunny locations this is easy, but it is more challenging in cloudy weather or at shaded sites. You will need a large enough solar array coupled with a good-size battery to get you through the night and less-than-ideal solar conditions.

These instructions are intended to allow you to build the NestCamDIY using inexpensive materials available on Amazon. You will need some basic skills in soldering, software, and, if you build your own birdbox, woodworking. This approach intentionally keeps soldering to a minimum, at the expense of some elegance in the design.

If you are willing to solder a bit more, you can create your own custom board to control the LEDs and motion detector. The downside of this approach is that it introduces additional failure points and can be difficult to debug unless you are proficient with a multimeter. The simplified setup below should work fine for most deployments.

## Important Caveats

> [!WARNING]
> This is intended for use on a private network and should not be exposed to the internet without additional security hardening.

> [!NOTE]
> Solar is an excellent way to power the NestCam, but you will need to select the right size solar panels and position them well enough to reliably power the system. That may take some tinkering and depends heavily on the time of year and your particular installation.

## 1. Raspberry Pi Configuration

### 1.1 Download Raspberry Pi Imager

Download the Raspberry Pi Imager here:

- <https://www.raspberrypi.com/software/>

You will need to insert your new SD card into your computer using an SD card reader.

<details>
<summary>Educational background</summary>

A Raspberry Pi is a tiny, inexpensive Linux computer. We use it here because it can control hardware through its GPIO pins, connect to Wi-Fi, run the camera and web server software, and do all of this with low power consumption in a very small package.

</details>

### 1.2 Install Raspberry Pi OS

Install Raspberry Pi OS on your SD card following the instructions provided by the imager.

- Select **Raspberry Pi Zero 2 W** as your device.
- Select the default full **Raspberry Pi OS (64-bit)** as your operating system. Testing with Lite and 32-bit systems did not yield meaningful power savings.
- Select the correct place to write the image. Be very careful here. You do **not** want to overwrite your hard drive or anything important. Check that the size shown matches the size of your SD card.
- Name your NestCam something easy to remember.
- Select your time zone and keyboard layout.
- Enter a username and password that you would like to use for the Pi.
- Enter the Wi-Fi network name and password that you would like the Pi to connect to. You can add additional networks later.
- Enable SSH. You can use either password or public-key authentication. Public-key authentication is more secure, but you will need to take an additional step. It is worth learning how to do this if you do not already know how, but if you are in a hurry, using a strong password is fine.
- Disable Raspberry Pi Connect.
- Double-check the selections and write the new image. This will take a minute or two.
- Once complete, remove the SD card.

<details>
<summary>Educational background</summary>

A public key is half of a cryptographic key pair used for logging in without typing a password. You place the public half on the Pi and keep the private half on your computer. This is usually more secure than password login and is also more convenient once set up.

</details>

### 1.3 Install the Heat Sink and Header Pins

Put the adhesive heat sink that comes with the Raspberry Pi onto the black processor chip, not the silver-colored metal box, which is the Wi-Fi chip.

Next, solder header pins onto the Raspberry Pi.

- You will need two rows of 20 header pins.
- Use clamps to hold them in place so they remain vertical and not slanted.
- The plastic pieces of the pins should be on the top of the board.
- Inspect afterward for solder bridges or bad solder joints.

Be careful to ensure the solder joints are solid, since bad soldering can lead to major debugging headaches later. Alternatively, if you do not want to do this, you can buy a pre-soldered version of the Pi.

### 1.3A Solar and Battery Only: Install the UPS HAT

Attach the UPS (Uninterruptible Power Supply) HAT to the Raspberry Pi using the standoff screws.

- Make sure the pogo pins on the HAT make good, clean contact with the bottom of the header pins on the Pi.
- Make sure the power switch is in the **off** position.
- Attach the battery to the HAT.
- Be extremely careful that the polarity is correct.
- Triple-check that the red wire on the battery leads to the `+` side of the battery connector on the HAT and the black wire leads to the `-` side.
- If you want to be extra careful, check the polarity with a multimeter.
- If you are using a larger battery than the one that comes with the UPS HAT, which is recommended for solar, use rubber bands to securely strap the Pi/HAT assembly to the battery itself.

### 1.4 Insert the SD Card and Power the Pi

Insert the newly written SD card into the Pi.

- If building a wired NestCamDIY, plug the Pi into a wall charger using the **PWR USB** connection.
- If using solar or battery, plug the power cable into the **USB-C power connection** on the UPS HAT.
- If you are using a UPS HAT, switch the unit on.

### 1.5 Connect to the Pi and Install the Software

You should see a green LED light on the Pi light up and flicker a bit. Wait until it is steady green, then try to connect to the Pi using your computer. It could take a few minutes for the Pi to boot up the first time.

In a Linux terminal, run:

```bash
ssh <NAME-OF-YOUR-PI>
```

This is the name you selected when you wrote the SD card, not your Wi-Fi network name or your username.

<details>
<summary>Educational background</summary>

A terminal is a text-based way of controlling a computer by typing commands. SSH, short for Secure Shell, lets you open a terminal session on another computer over the network. In this project, SSH is how you configure and manage the Pi without needing a monitor or keyboard connected to it.

</details>

If that fails, you will need to troubleshoot why the Pi is not connecting to Wi-Fi.

Once you have access to the Pi:

1. Wait for it to finish any initial update tasks.
2. Run `top` and watch until CPU usage comes down to a percent or two, then hit `q` to exit.
3. Install Git:

   ```bash
   sudo apt install git
   ```

4. Clone the NestCamDIY repository:

   ```bash
   git clone https://github.com/ehrenbrav/NestCamDIY
   ```

5. Go into the project directory:

   ```bash
   cd NestCamDIY
   ```

6. Install the software so all the pieces are put in the correct places on your Pi:

   ```bash
   sudo python setup.py
   ```

   It might take a while to install all required dependencies since you are starting with a very bare-bones operating system.

7. Shut down the Pi for now until it is needed again:

   ```bash
   sudo shutdown -h now
   ```

8. If you are using a UPS HAT, switch the on/off switch to **off** once the Pi LED shows the Pi is off. If you are using a wired setup, simply unplug the power supply once the LED turns off.

<details>
<summary>Educational background</summary>

The software installs a background service on the Pi that manages the camera, motion-triggered recording, infrared light control, and a small local web interface. That web interface lets you view the live stream, check status, and download recordings from another device on your network.

</details>

## 2. Build the Hardware

Now it is time to build the hardware. We will start with the controller board.

### 2.1 Use the Schematic as a Reference

Use the schematic at `[LINK]` as a reference.

The Pi both powers and controls the LEDs and motion detector using the GPIO pins. The LEDs are switched on and off using a MOSFET module, and the motion detector is a pre-built AM312 PIR sensor.

<details>
<summary>Educational background</summary>

GPIO pins are the Raspberry Pi's general-purpose input and output pins. They let the Pi read signals from devices such as the motion sensor and control devices such as the LEDs. A MOSFET is an electronic switch. Here, the Pi uses a GPIO pin to control the MOSFET, and the MOSFET switches the higher-current LED power on and off safely.

</details>

### 2.2 Create the LED Pigtails

You will need:

- two infrared LED pigtails
- one colored LED pigtail for testing

The procedure for all three is the same. Solder a red wire to the long lead of the LED and a black wire to the shorter lead. LEDs have polarity, so it is essential that current flows in the correct direction.

For all three pigtails:

1. Cut long pieces of red and black wire. Leave more length than you think you need so the wires can run from the top of the birdhouse back to the enclosure.
2. Strip about 1 centimeter of insulation from the wires using wire strippers.
3. Twist a 680-ohm resistor to the positive or negative lead of the LED.

   <details>
   <summary>Educational background</summary>
   
   Why do we use a resistor here, and why 680 ohms?
   
   </details>

4. Twist the stripped end of one wire around the other end of the resistor, and twist the other wire around the remaining LED lead.
5. Apply a small amount of solder to the three spliced areas so you have a secure electrical connection.
6. Cut a piece of heat-shrink tubing long enough to cover your splice.
7. Make sure the exposed conductor portions of the wires and leads cannot touch each other.
8. Use a blow dryer to gently heat the heat-shrink tubing until it contracts tightly around the splice.
9. Once you know the final length you need, cut the wires and strip about 1 centimeter of insulation from the ends.

### 2.3 Create the Motion Sensor Pigtail

Make a similar pigtail for the motion sensor.

You will need three wires:

- **yellow** for `3.3V`
- **black** for ground
- **green** for the motion signal

This sensor uses `3.3V` rather than the `5V` used with the LEDs, so yellow is used here to distinguish the lower-voltage supply.

<details>
<summary>Educational background</summary>

The positive supply is the wire that provides electrical voltage to a component. Ground is the return path that completes the circuit. Most small electronics need both connections to work: power flows from the positive supply, through the device, and back through ground.

</details>

The best approach is to make your own 3-wire cable to connect directly to the motion sensor.

- Use Dupont crimpers and connectors so no soldering is required.
- This gives you a secure connector that can be attached and reattached at exactly the length you need.
- Tutorial on using these connectors: `[LINK]`

Alternatively, you can buy pre-made jumper wires. You will need both female-female and male-female jumper wires.

### 2.4 Connect the Colored Test LED to the MOSFET Board

Connect the colored test LED pigtail to the MOSFET board.

- Loosen all four wire terminal screws.
- On the **load side** of the board, the side where the arrow is pointing, connect:
  - the **red** wire to the `+` terminal
  - the **black** wire to the `-` terminal

### 2.5 Connect Everything to the Pi

Get a good Raspberry Pi Zero 2 W pinout image for reference. Be very careful here, since it is easy to connect things to the wrong pins.

Make the following connections:

- Connect the yellow `3.3V` power supply for the motion detector to **Pin 1**.
- Connect the black ground for the motion detector to **Pin 6**. Any ground pin will work.
- Leave the green sensor wire unconnected for now.
- Connect a red jumper wire to the `+5V` **Pin 2** of the Pi.
- Connect the other end of this wire to the MOSFET board `+` power-supply terminal on the supply side.
- Connect a black jumper wire to **Ground Pin 20** of the Pi.
- Connect the other end to the MOSFET board `-` power-supply terminal on the supply side.
- Connect a blue jumper wire to **Pin 12**, which is `GPIO18`. This is the control used to turn the LEDs off and on and to dim them.
- Connect the other end of this wire to the `+` control pin of the MOSFET board.
- Connect a black ground jumper wire to **Pin 14** of the Pi.
- Connect the other end to the `-` control pin of the MOSFET board.

At this point, you should have the following wiring:

```text
Pin 1   (3.3V)   -> Yellow -> Motion detector + pin
Pin 2   (5V)     -> Red    -> MOSFET power supply + screw terminal (power supply side)
Pin 6   (GND)    -> Black  -> Motion detector - pin
Pin 12  (GPIO18) -> Blue   -> MOSFET + control pin
Pin 14  (GND)    -> Black  -> MOSFET - control pin
Pin 16  (GPIO23) -> Green  -> Motion detector signal pin (unconnected for testing)
Pin 20  (GND)    -> Black  -> MOSFET power supply - screw terminal (power supply side)
```

The red and black LED wires should be attached to the `+` and `-` screw terminals, respectively, on the load side of the MOSFET board.

### 2.5A Solar Setups Only: Create a Solar Power Cable

For solar setups, create a solar power cable.

1. Cut a 1-foot length of black and red wire.
2. Strip about 1 centimeter off each end.
3. The UPS HAT comes with a separate plastic connector for the solar hookup. Plug this into the jack on the UPS HAT and carefully note which side is `+` and which is `-`.
4. Using a precision screwdriver, loosen each of the two screws in this connector.
5. Insert the red wire into the `+` side and the black wire into the `-` side, then retighten the screws.
6. Make sure the wires are securely clamped.
7. Thread the two wires through heat-shrink tubing.
8. Splice the other ends of the wires to a pre-made JST connector by braiding the wires together, tinning them with a bit of solder, and applying heat-shrink tubing to protect the connection.

Alternatively, you could use Dupont connectors.

## 3. Bench Testing

Now it is time to run some initial tests to ensure the connections are good and the various pieces are working properly.

### 3.1 Connect the Camera

On the Pi, use your fingernail to carefully push both sides of the tiny black plastic connector bar straight out from the Pi. It should slide out about 1 millimeter. Be gentle, since you do not want to break this piece.

- Insert the ribbon cable with the metal contact strips facing the Pi.
- With the ribbon cable fully inserted, slide the black connector bar back into place.
- The ribbon cable should then feel securely connected and should not pull out easily.
- Repeat the same process on the camera side.
- On the camera side as well, the metal contact strips should face the board.

### 3.2 Boot the Pi

Boot the Pi by doing one of the following:

- If using the UPS HAT, turn the switch to **on**.
- If building a wired setup, plug a USB power charger into the **PWR** jack.

Wait for the green LED to come on and stop flashing. This may take a minute or two.

### 3.3 Run the LED and Motion Sensor Tests

Connect to the Pi over SSH as before:

```bash
ssh <NAME-OF-YOUR-PI>
```

Then go to the test directory:

```bash
cd test
```

Run the basic LED test:

```bash
./test_led.py
```

You should see the colored LED turn on and then turn off again.

Then:

1. Unplug the LED pigtail from the `LED1` connection.
2. Plug it into the `LED2` connection, again being careful about polarity.
3. Run the same test again:

   ```bash
   ./test_led.py
   ```

You should see the same behavior.

If either test fails, something is wrong. Most likely:

- the patch wires are connected to the wrong Pi pins, or
- there is a mistake in the control-board wiring

Next, run the motion sensor test:

```bash
./test_motion_detector.py
```

Wave your hand in front of the motion sensor. The LED should light up. If you stay still, the LED should go out again. Once you have verified that it works, hit `Ctrl-C` to stop the test.

If the motion test fails, check both the pin wiring and the control-board wiring.

<details>
<summary>Educational background</summary>

Commands that start with `./` run a program in the current folder. `Ctrl-C` stops a running program. `cd` changes folders. `sudo` runs a command with administrator privileges. `shutdown -h now` tells the Pi to stop running safely before power is removed.

</details>

### 3.4 Test the Camera

Run:

```bash
./test_camera.py
```

If the test fails, check the ribbon cable connection, especially that it is securely attached on each end and that the metal contacts are facing into each board.

### 3.4A Solar and Battery Setups: Test the UPS HAT

If you are using a UPS HAT, run:

```bash
./test_hat.py
```

If this fails, check that the pogo pins on the HAT are securely contacting the Pi pins.

### 3.5 Power Down the Pi

Power down the Pi:

```bash
sudo shutdown -h now
```

Disconnect the colored test LED. From here on, use the two infrared LEDs.

After you have verified that all system components are functioning, move on to the next step.

## 4. Birdhouse Assembly

The choice of birdhouse, or other habitat, is up to you. The size, shape of the entrance, and especially the location of the habitat determine the types of animals you will attract and how successful the setup will be.

You can either buy an off-the-shelf birdhouse or build your own. If you buy one, make sure it is large enough to accommodate the camera, motion sensor, and LEDs. You will need to attach the enclosure to the outside and drill holes for the LEDs, motion sensor, and ribbon cable.

### 4.1 Attach the Electronics to the Enclosure

Stick one piece of Velcro onto the back of the Pi, or onto the battery if using a UPS HAT, and another piece onto the inside of the enclosure.

Figure out where you want the USB cable to run, remove the electronics, and cut a notch in the enclosure to accommodate the cables.

This notch needs to accommodate:

- the USB or solar wire
- the motion sensor wire
- the two LED wires

A Dremel works well for this, but a small hacksaw can also work.

### 4.2 Mount the Enclosure to the Birdhouse

There should be four small holes in the back of the enclosure. Use `#4` wood screws to attach the enclosure to the side of the birdhouse.

If the enclosure does not already have holes, drill your own.

If the enclosure comes with a weatherproofing gasket, the long squishy strip, push it into the groove around the edge of the enclosure to help ensure a watertight seal.

### 4.3 Place the Camera in the Birdhouse

Place the camera in the center of the roof so it gets a good image of the entire interior.

- Use pushpins to attach the camera to the roof.
- Run the ribbon cable through a small notch drilled in the wall on the same side as the enclosure.

### 4.4 Drill Holes for the LEDs and Motion Sensor

- Drill two holes for the infrared LEDs in the roof of the birdhouse.
- These should be on a diagonal, on either side of where the camera will go.
- Drill one hole for the motion sensor near one of the other corners.

### 4.5 Install the LEDs and Motion Sensor

Insert the LEDs into the first two holes.

- Let them protrude about 2 millimeters into the birdhouse so they provide good illumination.
- Temporarily secure the pigtails to the roof with duct tape once you are happy with their placement.

Then do the same with the motion sensor.

Run all three cables back to the enclosure.

### 4.6 Put the Electronics Back Inside the Enclosure

Place the electronics back inside the enclosure.

- Put a small piece of Velcro on the back of the control board and secure it to the inside of the enclosure as well.
- If using solar or battery power, fully charge the UPS battery first so you have time to set everything up.

### 4.7 Reconnect the Ribbon Cable

Connect the ribbon cable to the Pi as before, if it is not already connected.

### 4.8 Reconnect the Control Board

Connect the control board to the correct Pi pins as before using the colored female-female wires.

### 4.9 Connect the Power Supply Cable

For powered setups:

- connect a 6-inch micro-USB extension cord into the **PWR** jack of the Pi

For battery and solar setups:

- connect a 6-inch USB-C extension cord to the charging jack on the UPS HAT

In all cases:

- route the cable through the notch in the enclosure
- make sure there is some slack both inside and outside the enclosure

For solar setups, also attach the solar power cable you previously made and route it through the notch.

## 5. Configuration

There are a number of settings you will need to modify, or may want to modify, to make your NestCamDIY work optimally. These will depend on the installation, especially the lighting, and on the type of camera you use.

> **TODO**
>
> Discuss the various settings that can be used in `/etc/nestcam/nestcam.env` to optimize picture quality.

## 6. Camera Choice

There are several different cameras you can choose from.

### Arducam for Raspberry Pi Camera Module 3, 12MP IMX708 75° Autofocus Noir Pi Camera V3

This camera worked well in very low light. The drawback is that it lacks an infrared filter, so colors under natural lighting are distorted and look pinkish.

### Waveshare IMX462 2MP IR-CUT Camera

This camera is specifically marketed as working well in low light while also supporting IR-cut behavior. The drawback is availability, since lead time may be two weeks or more depending on whether it is available on Amazon.

Both should work.

> **TODO**
>
> Discuss the other pros and cons of each camera.

### Waveshare IMX462 Configuration Change

With the Waveshare camera, you need to make the following changes to the configuration files for it to work.

Edit `/boot/firmware/config.txt` as follows:

- set `camera_auto_detect=0`
- add the line `dtoverlay=imx462`

Then save the file and reboot. Otherwise, the Pi will not automatically detect the camera.
