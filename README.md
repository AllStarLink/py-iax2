# Python IAX2 Library
This is a Python3 Library Implementing the IAX2 protocol as defined in
[RFC 5456](https://datatracker.ietf.org/doc/html/rfc5456). The
initial goal is a complete implementation of all of the
non-Media Message elements of IAX2 - *i.e.*, everything except 6.10-type
"mini-frames" that carry audio and video. Media mini-frames will either
be implemented later or will be something other users build on top of this
library as those tend to be implementation-specific.


