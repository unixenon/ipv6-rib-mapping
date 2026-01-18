# ipv6-rib-mapping
A mostly vibe coded but still cool project that makes a map of IPv6 prefixes in the Routing Information Base (or really any set of prefixes). It's meant to sort of recreate what bgp.tools has for their IPv4 internet map. Of course pinging every IPv6 address isn't really feasible, but it's still interesting to see what's registered.

Basically to get started:

1: Download the Routing Information Base. I used https://archive.routeviews.org/route-views6/bgpdata/

2: Extract the file: bunzip2 compressed_file.bunzip2

3: Install bgpdump on your system

4: Convert to text format: bgpdump extracted_file > text_rib.txt

5: Extract just the prefixes: cat text_rib.txt | grep PREFIX | sort | uniq | awk '{print $2}' > prefixes.txt

6: Install python dependencies and run file. By default it expects prefixes.txt in the same directory.

