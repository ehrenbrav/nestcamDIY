The NestCamDIY is a Raspberry Pi based video camera that can be installed in a birdhouse, squirrel-house or any other animal dwelling. Depending on how you build it, it can be powered by a wired power supply, a battery, or solar panels. It works with both ambient light and in complete darkness and streams a video feed to an address on your network, meaning you can view it using any browser. This allows live viewing from you phone, a computer, or even a dedicated video monitor. The interior of the box is illuminated by infrared lights, which are invisible to both birds and humans but make the image show up clearly on video (though with distorted colors). It incorporates a motion sensor that starts recording video anytime motion is detected, which can be downloaded and viewed via the webpage.

The simplest power setup is just to plug it in. You can run an outdoor extension cord to the birdhouse, plug in an outdoor USB charger and connect this to the device. Alternatively, you can use either a battery or solar setup. Both involve using an uninterruptible power supply to give the device power while you are swapping out the battery or when it is dark out. You can have leave a weatherproof battery somehwere convenient like at the base of the tree, and run a USB charging cord from it to the device. For solar, you'll need to experiment to find a suitable size solar panel and location. In sunny locations, this is easy, but definitely more challenging in cloudy weather or shaded sites. You'll need to use a large enough solar array coupled with a good size battery to get you through the night and less than ideal solar conditions.

These instructions are intended to allow you to build the NestCamDIY using inexpensive materials available on Amazon. You will need some basic skills in soldering, software, and (if you build your own birdbox) woodworking. This intentionally keeps soldering to a minimum, at the expense of some elegance of the design. If you are willing to solder a bit more, you can create your own custom board to control the LEDs and motion detector. The downside with this approach is that it introduces numerous additional failure points and can be difficult to debug unless you are proficient with a multimeter. Hardware is hard! But the simplified setup below should work just fine for most deployments.

Important Caveats
This is intended for use on a private network and should not be exposed to the internet without additional security hardening.
Solar is definitely an awesome way to power the netcam! But if you are using solar power, you will need to select the right size solar panels and positioning to provide enough juice to reliably power the system. That could take some tinkering and definitely depends on the time of year and your particular situation. There is an entire section on this below.

Raspberry Pi Configuration
1.1 Download the Raspberry Pi Imager from here: https://www.raspberrypi.com/software/. You'll need to insert your new SD card into your computer using an SD card reader.
-- Educational Background: What is a Raspberry Pi and why are we using one?

1.2. Install the Raspberry Pi OS on your SD card following the instructions provided by the imager:
- Select "Raspberry Pi Zero 2 W" as your device.
- Select the default full Raspberry Pi OS (64-bit) as your operating system. Testing with Lite and 32-bit systems did not yield any meaningful power savings.
- Select the correct place to write the image. Be very careful here - you do NOT want to overwrite your hard drive or anything important! Check that the size indicated matches the size of your SD card.
- Name your nestcam something easy to remember.
- Select your timezone and keyboard layout.
- Enter a username and password that you'd like to use for the Pi.
- Enter the wifi network (the name and password) that you'd like the Pi to connect to. You can add additional networks later.
- Enable SSH. You can use either password or public key authentication. Public key authentication is more secure but you'll need to take an additional step (just follow the instructions). It is worth learning how to do this if you don't know, but if you are in a hurry or frustrated, using a password is fine too so long as it is a strong one.
-- Educational Background: What is a public key and why should I use one?
- Disable Raspberry Pi Connect.
- Double-check the selections and write the new image. This will take a minute or two. Once complete, remove the SD card.

1.3. Put the adheasive heat sink that comes with the Raspberry Pi onto the black processor chip (not the silver colored metal box - that's the wifi chip). Next, solder header pins onto the Raspberry Pi. You'll need two rows of 20 header pins. When you solder these, use clamps to hold them in place so they are vertical and not slanted. The plastic pieces of the pins should be on the top of the board. As always, inspect afterwards for solder bridges or bad solder joints. Be careful to ensure the solder joints are 100% good, as bad soldering can lead to major debugging headaches later with hard-to-diagnose failures! Alternatively, if you don't want to do this, you can buy a pre-soldered version of the Pi.

1.3A. (Solar and Battery Only) Attach the UPS (Uninterruptable Power Supply) hat to the Raspberry Pi using the standoff screws. Be sure that the pogo pins on the hat make good, clean contact with the bottom of the header pins on the Pi. Make sure the power switch is in the off position and attach the battery to the hat. Be extremely careful that the polarity is correct here! Triple-check that the red wire on the battery leads to the + side of the battery connecter on the hat and the black wire leads to the - side. For the super-paranoid, check the polarity with a multimeter. If you are using a larger battery than what comes with the UPS hat (recommended for solar), use rubber bands to securely strap the Pi/Hat to the battery itself. 

1.4. Insert the newly-written SD card into the Pi. Plug in the Pi using the PWR USB connection to a wall charger (if building a wired NestCamDIY) or to the USB-C power connection on the UPS hat (if using solar/battery). If you are using a UPS hat, switch the unit on.

1.5. You should see a green LED light on the Pi light up and flicker a bit. Wait until it is steady green and try to connect to the Pi using your computer - it could take a few minutes for the Pi to come up the first time, so be patient:
- In a linux terminal, run `ssh <NAME-OF-YOUR-PI>`. This is the name you selected when you wrote the SD card (not your wifi SSID or your username).
-- Educational Background: What is a terminal?
-- Educational Background: What is SSH? [ ]
- If this fails, you'll need to troubleshoot why the Pi is not connecting to your wifi. Use ChatGPT (or similar) troubleshoot this common issue, as there are a number of potential causes. 
- Once you have access to the Pi, wait for it to finish any initial update tasks. Run `top` and watch until the CPU usage comes down to a percent or two, then hit `q` to exit.
- Install git by running `sudo apt install git`.
-- Educational Background: What is git? [ ]
- Next, use git to clone the NestCamDIY repository: `git clone https://github.com/ehrenbrav/NestCamDIY`. This will put a copy of all the NestCamDIY software on your Pi.
- Go into the project directory: `cd NestCamDIY`. Remember you can hit Tab for autocomplete to make typig faster.
- Install the software, so all the pieces are put in the correct places on your Pi: `sudo python setup.py`. It might take a bit to install all the required dependencies since we're starting with a very bare-bones operating system. Once that completes successfully, shutdown the Pi for now until we need it again: `sudo shutdown -h now`. If you are using a UPS hat, switch the on-off switch to off once the LED on the Pi shows that it is off. If you are using a wired setup, simply pull out the power supply once the LED turns off.
-- Educational Background: How the NestCamDIY software works. [ ]

Build the Hardware
Now it's time to build the hardware we'll need. We'll start with the controller board.
2.1. Use the schematic [LINK] as a reference. The Pi both powers and controls the LEDs and motion detector using the GPIO pins. The LEDs are switched on and off using a MOSFET module and the motion detector is a pre-built AM312 PIR sensor. 
-- Educational Background: What are GPIO pins? [ ]
-- Educational Background: What is a MOSFET?

2.2. Create the test and production LED pigtails. You will need two infrared LED pigtails and one colored LED pigtail for testing (since obviously you cannot see the infrared directly and thus would have no easy way of checking that everything is working). The procedure for all three is the same. Solder a red wire to the long lead of the LEDs and a black wire to the shorter leads. LEDs have a polarity, so it is essential that you have the current flowing in the correct direction. For all of this, follow this procedure:
- Cut long-ish pieces of red and black wire. You want to ensure you have enough length to connect the LEDs at the top of the bird house all the way back to the enclosure, so leave plenty to spare. You can always cut away the excess as necessary.
- Strip the insulation off about one centimeter of the wires using wire strippers.
- Twist a 680-ohm resistor to the positive (or negative) lead of the LED.
-- Educational Background: Why do we use a resistor here and why 680-ohms?
- Twist the stripped end of the wire around the other end of the resistor, and twist the other wire around the other lead of the LED.
- Apply a small amount of solder to the three spliced areas so you have a very secure electrical connection.
- Cut a piece of heat-shrink tubing long enough to cover your splice. What you want is to avoid a short - where your red and black wires touch together. So it's important that you ensure that the conductor portions of the wires and leads never touch each other.
- Use a blow-dryer to gently heat the heat-shrink wrap until it contracts tightly around the splice.
- When know the length you need, cut the wires to this length and strip the insulation off about one centimeter off the red and black wires. You can do the cutting later once you know the actual dimensions of your final product.

2.3. Next make a similar pigtail for the motion sensor. You will need three wires for the motion sensor: yellow, black, and green. Here, this sensor uses 3.3V rather than the 5V that we use with the LEDs. So we use yellow to represent the positive power supply here to distinguish it from the 5V power supply. Green represents the signal - whether motion is detected or not. Black, as is typical, is ground. 
-- Educational Background: What do the positive power supply and ground mean?
- Here, the best way to do this is to make your own 3-wire cable to connect directly to the motion sensor. You need to use the Dupont cripers and connectors for this - no soldering required, and you'll have a secure connector that you can attach and reattach in the exact length you need. Tutorial on using these connectors is here: [  ]. Alternatively, you can simply buy pre-made jumper wires - you'll need both female-female and male-female jumper wires. This is super easy - just push them on to the header pins of the motion detector and connect enough together to get the length that you need.

2.4. Next, connect the colored testing LED pigtail to the MOSFET board. You'll need to loosen all four of the wire terminal screws. On the load side (the side of the board where the arrow is pointing), connect the red wire to the + terminal and the black wire to the - terminal. 

2.5. Now we'll connect everything to the Pi. Get a good image of the Raspberry Pi Zero 2W pinout for reference - this shows you which header pin is which. Be very careful here - it is easy to connect things to the wrong pins!
- Connect the yellow 3.3V power supply for the motion detector to Pin 1.
- Connect the black ground for the motion detector to Pin 6 (but any of the Ground pins will work as well).
- Leave the green sensor wire unconnected for now.
- Connect a red jumper wire to the +5V Pin 2 of the Pi.
- Connect the other end of this wire to the MOSFET board's power supply - the + wire terminal opposite to the one you connected the colored LED to. Make sure you screw each of these terminals securely onto the wires.
- Connect a black jumper wire to Ground Pin 20 of the Pi.
- Connect the other end to the MOSFET board's power supply ground - the "-" wire terminal opposite where the LED connects to.
- Connected a blue jumper wire to Pin 12 of the Pi, which is "GPIO18". This will be the control to turn the LEDs off and on (and dim them).
- Connect the other end of this to the + control pin of the MOSFET board. This is one of the two header pins that stick out the side of the board.
- Connect a black ground jumper wire to Pin 14 of the Pi.
- Connect the other end of this to the - contol pin of the MOSFET board.
- At this point, you have made all the necessary connections to the Pi: 
Pin 1   (3.3V)   -> Yellow -> Motion detector + pin.
Pin 2   (5V)     -> Red    -> MOSFET power supply + screw terminal (the power supply side)
Pin 6   (GND)    -> Black  -> Motion detector - pin
Pin 12  (GPIO18) -> Blue   -> MOSFET + pin
Pin 14  (GND)    -> Black  -> MOSFET - pin
Pin 16  (GPIO23) -> Green  -> Motion detector signal pin (unconnected for testing)
Pin 20  (GND)    -> Black  -> MOSFET power supply - screw terminal (the power supply side)
The red and black LED wires should be attached to the + and - screw terminals (respectively) on the load side of the MOSFET board.

2.5A. For solar setups, you also need to create a solar power cable:
- Cut a 1 foot length of black and red wire.
- Strip about 1 centimeter off each end.
- The UPS hat comes with a separate plastic connector for the solar hookup. Plug this into the jack on the UPS hat and carefully note which side is + and which is -: these are indicated on the board itself. As usual, you need to be very careful about the polarity here!
- Using a precision screwdriver, loosen each of the two screws in this connector. Insert the red wire into the + side and the black wire into the - side and retighten the screws. The wires should be securely clamped to the connector.
- Thread the two wires through heat-shrink wrap.
- Splice the other ends of the wires to a pre-made JST connectors: braid the wires together, tin them with a bit of solder, and apply heat shrink wrap to protect the connection. Alternatively, you could use the Dupont connectors.

Bench Testing
Now it is time to run some initial tests to ensure the connections are good and the various pieces are working properly.
3.1. Connect the camera. On the Pi, use your fingernail to carefully push both sides of the tiny black plastic connector bar straight away from the Pi. It should slide out about a milimeter - be gentle here as you don't want to break this piece. Insert the ribbon cable, with the metal contact strips facing the Pi. With the ribbon cable fully inserted, slide the black bar of the connector back into place. Once done, the ribbon should feel very securely connected to the Pi and should not be easily pulled out. Next, do the exact same thing for the camera side. Remember the metal contact strips on the ribbon cable face the board.

3.2. Boot the Pi by turning the off-on switch to on (if using the UPS hat) or plugging a USB power charger into the PWR jack (if buiding a wired setup). Wait for the green LED to come on and stop flashing (this might take a minute or two as the Pi boots).

3.3. Connect to the Pi using ssh as before: `ssh <NAME-OF-YOUR-PI>`. Remember to be patient as it might take a bit of time for the Pi to boot. If you wait a good five minutes and still can't access it, use ChatGPT to debug the issue, as it could be a number of common causes.
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

4.9. Connect the power supply cable. For powered setups, connect a 6 inch micro-USB extension cord into the PWR jack of the Pi. Route the cable through the notch in the enclosure, ensuring there is some slack both inside and outside of the enclosure. For battery and solar setups, connect a 6 inch USB-C extension cord into the charging jack of the Pi as above. For solar setups, in addition to this charging cable, attach the solar power cable you previously made and route it through the notch. 



CONFIGURATION
There are a number of setting you will need to modify (or may want to modify to make your NestCamDIY work optimally). 
