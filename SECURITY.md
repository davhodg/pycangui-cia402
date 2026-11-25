# Security policy

## Reporting a vulnerability

Please report it privately rather than in a public issue:
**[report a vulnerability](https://github.com/davhodg/pycangui-cia402/security/advisories/new)**,
which is also under the repository's *Security* tab. Say what you found, how to make it
happen, and which version of the plugin and of pycangui you were running.

One that is in pycangui rather than in this plugin goes to
[pycangui's security policy](https://github.com/davhodg/pycangui/security/policy), whose
rules apply here too: no promised response time, every report read and answered, and the
fix and the advisory published together.

## Versions that get fixes

The latest release and `master`.

## What is and is not a vulnerability

A plugin is code that pycangui runs, and this one drives motors. A drive that moves when
it is enabled is doing what it was asked; what it does is the drive's and its setup's.

What **is** worth reporting: the plugin writing to a drive that nobody asked it to -- an
enable without the question it asks first, a demand sent when its pane is put away or
pycangui closes, or a halt it says it sent and did not.
