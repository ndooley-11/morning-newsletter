# Daily 7 a.m. TV setup

## What is and is not automatic

The public newsletter is https://ndooley-11.github.io/morning-newsletter/ . GitHub runs collection independently of the owner's computer. Collection is attempted at 6:17 a.m. America/New_York (DST-aware); GitHub scheduling can be delayed. The viewer checks JSON every five minutes and labels stale or failed updates.

**Cloud deployment does not turn the TV on.** The TV wake routine and browser launch are separate, physical-device acceptance gates. No TV wake routine has been created or verified by this repository.

## Generic setup for a compatible smart TV

1. Leave the TV plugged in and connected to its network. Enable network standby if supported, using the manufacturer's manual. Menu labels and availability vary by model; do not change service-menu options.
2. In a compatible TV browser, open the public URL above and save it as a bookmark. Test closing/reopening the browser: it must return to that page, not a search screen. Use the browser's fullscreen setting if webpage fullscreen is unavailable.
3. If a launcher supports opening an app on wake, configure it to open the browser. Test a real standby/wake cycle: opening the launcher alone is not proof that it launches the browser. If startup-on-wake is unsupported, this gate remains unresolved; do not substitute a boot-only setting without testing standby.
4. In an authorized smart-home controller, first test whether its device control can turn the TV on from standby, after it has been off for at least 10 minutes. If it cannot, do not assume a routine will fix it: check network standby and device connectivity first.
5. If power-on works and timed device actions are supported, create an automation with a time starter at **7:00 a.m.**, every day, in the intended local time zone; action **turn on the TV**. Use an explicit power-on action, not a power toggle, because a toggle could turn an already-on TV off. Availability depends on the controller and TV.
6. Test the complete routine at a temporary time a few minutes ahead, with the TV in standby. Restore the starter to 7:00 a.m. every day after the test. Do not count pressing Run in the app as proof that its time trigger fired.
7. If a streaming device is connected, test returning to its HDMI input with its remote. HDMI-CEC behavior must be tested on the actual setup. DNS changes are not needed for TV power or input control.

## Acceptance checklist

- [ ] The authorized controller can wake the TV after extended standby.
- [ ] A real timed routine wakes it without the laptop running.
- [ ] The configured launcher opens the browser after standby, not only a full reboot.
- [ ] The browser opens the newsletter bookmark automatically.
- [ ] Headlines and bottom ticker are readable without overscan.
- [ ] Reload retrieves the public edition; date and stale warning are visible.
- [ ] The usual remote returns to normal viewing.
- [ ] Final routine is enabled for 7:00 a.m. every day in the intended home time zone.

## Security and verification boundaries

Do not enable unauthenticated remote debugging, open router ports, or put household/device credentials, inventory, or diagnostics in a public repository. Remote setup needs an authorized controller session or a paired TV control connection. Browser emulation cannot verify physical power, startup, or remote-control behavior.

## Official references

- Google routines, including time starters: https://support.google.com/assistant/answer/7672035
- TCL Google TV power-on behavior: https://support.tcl.com/en_US/google-tv-power-on-options
- TCL Google TV settings and built-in manual: https://support.tcl.com/en_US/us-googletv-setup-configuration/tcl-google-tv-settings-and-user-manual-guide
- TCL Google TV Network Standby example (regional P745 manual; not universal model support): https://www.tcl.com/content/dam/brandsite/region/australia/AU_Mediacenter/download/P745_Series_Owners_Manual.pdf

These references establish available feature families, not acceptance on a particular installed firmware or Google Home account.
