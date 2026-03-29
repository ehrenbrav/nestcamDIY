The NestCamDIY is a Raspberry Pi based video camera that can be installed in a birdhouse, squirrel-house or any other animal dwelling. Depending on how you build it, it can be powered by a wired power supply, a battery, or solar. It works with both ambient light and in complete darkness and streams a video feed to an address on your network, meaning you can view it using any browser. This allows live viewing from you phone, a computer, or even a dedicated video monitor.

These instructions are intended to allow you to build the NestCamDIY using inexpensive materials available on Amazon. You will need some basic skills in soldering, software, and (if you build your own birdbox) woodworking.

Important Caveats
This is intended for use on a private network and should not be exposed to the internet without additional security hardening.

Raspberry Pi Configuration
1.1 Download the Raspberry Pi Imager from here: https://www.raspberrypi.com/software/.

1.2. Install the Raspberry Pi OS on your SD card following the instructions. You will need a name for your NestCamDIY, a RSA key or password that you'll use to access the Pi, and your wireless SSID and password.
-- Educational Background: What is an RSA key and why should I use one?

1.3. Put the adheasive heat sink that comes with the Raspberry Pi onto the black processor chip (not the silver colored metal box - that's the wifi chip). Next, solder header pins onto the Raspberry Pi. You'll need two rows of 20 header pins. When you solder these, use clamps to hold them in place so they are vertical and not slanted. The plastic pieces of the pins should be on the top of the board. As always, inspect afterwards for solder bridges or bad solder joints. Be careful to ensure the solder joints are 100% good, as bad soldering can lead to major debugging headaches later with hard-to-diagnose failures!

1.3A. (Solar and Battery Only) Attach the UPS (Uninterruptable Power Supply) hat to the Raspberry using the standoff screws. Be sure that the pogo pins on the hat make good, clean contact with the bottom of the header pins on the Pi. Make sure the power switch is in the off position and attach the battery to the hat. Be extremely careful that the polarity is correct here! Triple-check that the red wire on the battery leads to the + side of the battery connecter on the hat and the black wire leads to the - side. For the super-paranoid, check the polarity with a multimeter. If you are using a larger battery than what comes with the UPS hat, use rubber bands to securely strap the Pi/Hat to the battery itself. 

1.4. Insert the newly-written SD card into the Pi. Plug in the Pi using the PWR USB connection to a wall charger (if building a wired NestCamDIY) or to the USB-C power connection on the UPS hat (if using solar/battery). If you are using a UPS hat, switch the unit on.

1.5. You should see a green LED light on the Pi light up and flicker a bit. Wait until it is steady green and try to connect to the Pi using your computer:
- In a linux terminal, run `ssh <NAME-OF-YOUR-PI>`. This is the name you selected when you wrote the SD card (not your wifi SSID or your username).
-- Educational Background: What is SSH? [ ]
- If this fails, you'll need to troubleshoot why the Pi is not connecting to your wifi. Use ChatGPT (or similar) troubleshoot this common issue, as there are a number of potential causes.
- Once you have access to the Pi, install git by running `sudo apt install git`.
-- Educational Background: What is git? [ ]
- Next, use git to clone the NestCamDIY repository: `git clone https://github.com/ehrenbrav/NestCamDIY`. This will put a copy of all the NestCamDIY software on your Pi.
- Install the software, so all the pieces are put in the correct places on your Pi: `sudo python install.py`. Once that completes successfully, shutdown the Pi for now until we need it again: `sudo shutdown -h now`. If you are using a UPS hat, switch the on-off switch to off once the LED on the Pi shows that it is off. If you are using a wired setup, simply pull out the power supply once the LED turns off.
-- Educational Background: How the NestCamDIY software works. [ ]

Build the Hardware
Now it's time to build the hardware we'll need. We'll start with the controller board.
2.1. Use the schematic [LINK] as a reference. We'll be building this board to control the infrared LEDs and the motion detector. The Pi both powers these and controls them using the GPIO pins. First, solder the header pins into the perf board. These are the pins we'll use to connect to the Pi. Inspect your soldering.
-- Educational Background: What are GPIO pins? [ ]

2.2. Solder the wire connectors for the LEDs and the motion sensor. These will allow us to easily connect and disconnect the infrared LEDs and motion sensor wires to the board. As always, inspect your soldering to ensure your connections are 100% good.

2.3. Solder the components onto the board: the resistors, capacitor, and MOSFET transistor. Put each of these components into the board, bend the leads so they stay in place, solder each of them to the board, and then use flush cutters to clip the excess leads so everything is neat and tidy. Inspect your soldering.
-- Educational Background: Hoes does this board work? [ ]

2.4. Connect the components together by soldering wires to the back of the board. Be very careful here to get the correct wires soldered to the correct components.

2.5. Create the test and production LED pigtails. You will need two infrared LED pigtails and one colored LED pigtail for testing (since obviously you cannot see the infrared directly and thus would have no easy way of checking that everything is working). The procedure for all three is the same. Solder a red wire to the long lead of the LEDs and a black wire to the shorter leads. LEDs have a polarity, so it is essential that you have the current flowing in the correct direction. For all of this, follow this procedure:
- Cut long-ish pieces of red and black wire. You want to ensure you have enough length to connect the LEDs at the top of the bird house all the way back to the enclosure, so leave plenty to spare. You can always cut away the excess as necessary.
- Strip the insulation off about two centimeters of the wire using wire strippers.
- Twist the lead and the wire together so they stay connected.
- Apply a small amount of solder to the spliced area so you have a very secure electrical connection.
- Cut a piece of wire wrap tubing long enough to cover your splice. What you want is to avoid a short - where your red and black wires touch together. So it's important that you ensure that the conductor portions of the wires and leads never touch each other.
- Use a blow-dryer to gently heat the heat-shrink wrap until it contracts tightly around the splice.
- Once you have both the black and red wires connected to the leads, thread these through a larger piece of heat-shrink wrapping. Use a blow-dryer to heat the heat-shrink wrapping so it contracts around the wires.
- When know the length you need, cut the wires to this length and strip the insulation off about two centimeters off the red and black wires. You can do the cutting later once you know the actual dimensions of your final product.

2.6. Next make a similar pigtail for the motion sensor. You will need three wires for the motion sensor: yellow, black, and green. Here, this sensor uses 3.3V rather than the 5V that we use with the LEDs. So we use yellow to represent the positive power supply here to distinguish it from the 5V power supply. Green represents the signal - whether motion is detected or not. Black, as is typical, is ground. The same principle applies here - strip two centimeters off the wires, twist them around the leads (red to +, black to GND, and yellow to ****), apply solder to each of these splices to get a good connection, put a small piece of heat-shrink wrap around each splice to ensure they don't touch each other, and thread all three wires through heat-shrink wrap. As before, use hot air to get the heat-shrink wrap to compress tightly around the wires.
-- Educational Background: What do the positive power supply and ground mean?

2.7. Connect the colored LED wires (with two centimeters stripped off the end) to the LED1 connection of the board. Double-check the polarity again, since LEDs need to have the positive lead connected to the positive side of the circuit and the negative lead attached to the negative side.

2.8. Connect the control board to the Pi. Using a red, yellow, blue, green, and black female-female wire, attach the control board to the Pi. Red represents the 5V power supply, yellow is the 3.3V power supply, blue is the control signal for the LEDs, green is the signal from the motion sensor, and black is ground. Connect these to the Pi's header pins as follows, using the pinout diagram [LINK] for reference:
Pin 1   (3.3V)   -> Yellow
Pin 2   (5V)     -> Red
Pin 6   (GND)    -> Black
Pin 12  (GPIO18) -> Blue
Pin 16  (GPIO23) -> Green

2.8A. For solar setups, you also need to create a solar power cable:
- Cut a 1 foot length of black and red wire.
- Strip about 1 centimeter off each end.
- The UPS hat comes with a separate plastic connector for the solar hookup. Plug this into the jack on the UPS hat and carefully note which side is + and which is -: these are indicated on the board itself. As usual, you need to be very careful about the polarity here.

- Using a precision screwdriver, loosen each of the two screws in this connector. Insert the red wire into the + side and the black wire into the - side and retighten the screws. The wires should be securely clamped to the connector.
- Thread the two wires through heat-shrink wrap.
- Splice the other ends of the wires to one of the JST connectors: braid the wires together, tin them with a bit of solder, and apply heat shrink wrap to protect the connection.

Bench Testing
Now it is time to run some initial tests to ensure the connections are good and the various pieces are working properly.
3.1. Connect the camera. On the Pi, use your fingernail to carefully push both sides of the tiny black plastic connector bar straight away from the Pi. It should slide out about a milimeter - be gentle here as you don't want to break this piece. Insert the ribbon cable, with the metal contact strips facing the Pi. With the ribbon cable fully inserted, slide the black bar of the connector back into place. Once done, the ribbon should feel very securely connected to the Pi and should not be easily pulled out. Next, do the exact same thing for the camera side. Remember the metal contact strips on the ribbon cable face the board.

3.2. Boot the Pi by turning the off-on switch to on (if using the UPS hat) or plugging a USB power charger into the PWR jack (if buiding a wired setup). Wait for the green LED to come on and stop flashing (this might take a minute or two as the Pi boots).

3.3. Connect to the Pi using ssh as before: `ssh <NAME-OF-YOUR-PI>`.
- Change to the test directory: `cd test`.
- Run the basic LED test: `./test_led.py`. You should see the colored LED turn on and then turn off again. Unplug the LED pigtail from the LED1 connection and plug it into the LED2 connection, again being careful as to the polarity. Run `./test_led.py` again. You should see the same thing. If either test failed, something is wrong - most likely you hooked up the patch wires to the wrong Pi pins or made a mistake assembling the control board. You'll need to troubleshoot this before continuing.
- Run the motion sensor test: Run `./test_motion_detector.py`. Wave your hand in front of the motion sensor - it should cause the LED to light up. If you stay still, the LED should go out again. Once you've verified that this is working, hit Ctrl-C to stop the test. Again, if the test fails, check that you hooked up the wires to the correct pins and check the wiring of your control board. 
-- Educational Background: What do these commands actually mean?

3.4. Test the camera. First, we need to ensure the camera is properly connected and operational.
- Run `./test_camera.py`. If the test fails, check the ribbon cable connection, especially that it is securely attached on each end and the metal contacts are facing into each board.

3.4A. Test the UPS hat. If you are using solar/battery and have a UPS hat, do the following:
- Run `./test_hat.py`. If this fails, check that the pogo pins on the hat are securely contacting the pins of the Pi.

3.5. Power down the Pi: `sudo shutdown -h now`. Disconnect the colored testing LED. From here on, we'll use the two infrared LEDs. 

After we have varified that all of the system components are functioning, we can move on to the next step.

Birdhouse Assembly
The birdhouse (or other habitat) that you use depends on you. The size, shape of the entrence, and (above all) the location of the habitat determine the type of animals you will attract and the ultimate success of being able to get someone living in your house. You can either buy an off-the-shelf birdhouse or build your own. If you buy one, just make sure that it is big enough to accomodate the small camera, motion sensor, and LEDs. You'll need to attach the enclosure to the outside and drill holes to insert the LEDs, motion sensor, and ribbon cable. The rest is entirely up to you - for birds, you can partially select the species you attract based on the size of the entrance hole and location of the dwelling.

4.1 Attach the electronics to the inside of the enclosure. Stick a piece of velcro onto the back of the Pi or battery (is using a UPS hat)  and another onto the inside of the enclosure. Figure out where you want the USB cable to run, remove the electronics, and cut a notch in the enclosure to accomodate the cables. This notch will need to accomodate a USB/solar wire, the motion sensor wire, and the two LED wires. A Dremel tool works perfectly for this, but you can also just use a small hacksaw. 

4.2. Mount the enclosure to the birdhouse. There should be four tiny holes in the back of the enclosure - use #4 wood screws to screw the enclosure into the side of the birdhouse. If the enclosure doesn't have preexisting holes, just drill your own using an electric drill. If the enclosure comes with a weatherproofing gasket (a long squishy string), push this into the groove in the edge of the enclosure - this helps ensure a watertight seal.

4.3. Place the camera in the birdhouse. The camera should be in the center of the roof to get a good image of the entire interior. Use pushpins to attach the camera to the roof and run the ribbon cable through a small notch drilled in in the wall on the same side as the enclosure. 

4.4. Drill two holes for the infrared LEDs in the roof of the birdhouse. These should be on a diagonal on either side of where the camera will go. Then drill a single hole for the motion sensor. This should be on close to one of the other corners.

4.5. Insert the LEDs into the initial two holes. You want the LEDs protruding maybe two milimeters into the birdhouse - enough to provide good illumination. Duct tape the pigtails temporarily to the roof of the birdhouse once you are happy with the placement. Next, do the same with the motion sensor. Run all three cables back to the enclosure.

4.6. Place the electronics back inside the enclosure. Put a small piece of velcro on the back side of the control board and secure this to the inside of the enclosure as well. If using solar/battery power, charge up the UPS battery fully to give yourself time to set everything up.

4.7. Connect the ribbon cable to the Pi as before, if not already connected.

4.8. Connect the control board to the correct pins of the Pi as before, using the colored female-female wires.

4.9. Connect the power supply cable. For powered setups, connect a 1 foot micro-USB extension cord into the PWR jack of the Pi. Route the cable through the notch in the enclosure, ensuring there is some slack both inside and outside of the enclosure. For battery and solar setups, connect a 1 foot USB-C extension cord into the charging jack of the Pi as above. For solar setups, in addition to this charging cable, attach the solar power cable you previously made and route it through the notch. 



CONFIGURATION
There are a number of setting you will need to modify (or may want to modify to make your NestCamDIY work optimally). 
