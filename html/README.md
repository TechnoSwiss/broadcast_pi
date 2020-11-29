Sample webpage controls for broadcast script USE AT YOUR OWN RISK

Web page uses control files (pause/extend) and status file (status) from broadcst.py to allow some limited control and status of running scripts. Becaue control passing is all done via file, webserver needs to be running on a system that broadcast.py has filesystem access to.

There is currently no security on these web pages, so anybody who can access the webserver can control broadcast, webserver running these pages should NOT be made accessable to the internet. Ideally webserver should be on a network only accessable to those you wish to control the broadcasts.

You will need to download the latest verison of jquery from https://jquery.com/download/ and update index.html accordingly, this was tested with jquery version 3.5.1
