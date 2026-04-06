# =============================================================================
# Varnish VCL template — Docker / LEco adaptation
# =============================================================================
# Copy your production VCL here and apply these Docker adaptations:
#
#   1. BACKENDS: Replace localhost/127.0.0.1 with Docker container_name
#      e.g.  .host = "my-app-server";  .port = "3000";
#
#   2. ACL: Add Docker bridge ranges for PURGE/BAN
#      "172.16.0.0"/12;   # Docker bridge networks
#      "10.0.0.0"/8;      # Docker overlay / AWS VPC
#
#   3. UNUSED BACKENDS: Remove or use all declared backends.
#      Varnish 7.6+ is strict — unused backends cause compilation errors.
#
#   4. VCL VERSION: Use vcl 4.1; for Varnish 7.x features (resp.body, etc.)
#
#   5. HOST ROUTING: Remove host-based backend switching if you have a single
#      backend in Docker (no localhost vs production distinction).
#
# This file is bind-mounted into the Varnish container:
#   volumes:
#     - ./conf/varnish/default.vcl:/etc/varnish/default.vcl:ro
# =============================================================================

vcl 4.1;

import std;

backend default {
    .host = "my-app-server";                # ← container_name of your Express/API service
    .port = "3000";                         # ← port your app listens on
    .connect_timeout = 5s;
    .first_byte_timeout = 600s;
    .between_bytes_timeout = 60s;
}

acl purge {
    "localhost";
    "127.0.0.1";
    "172.16.0.0"/12;                        # Docker bridge networks
    "10.0.0.0"/8;                           # Docker overlay / AWS VPC
    "192.168.0.0"/16;                       # Host-network / local network
}

sub vcl_recv {
    set req.backend_hint = default;

    # PURGE support
    if (req.method == "PURGE") {
        if (!client.ip ~ purge) {
            return (synth(405, "Not allowed."));
        }
        return (purge);
    }

    # BAN support
    if (req.method == "BAN") {
        if (!client.ip ~ purge) {
            return (synth(403, "Forbidden"));
        }
        if (!req.http.X-Ban-URL) {
            return (synth(400, "X-Ban-URL header required"));
        }
        if (req.http.X-Ban-Exact == "true") {
            ban("req.url == " + req.http.X-Ban-URL);
        } else {
            ban("req.url ~ " + req.http.X-Ban-URL);
        }
        return (synth(200, "Banned"));
    }

    # Only cache GET and HEAD
    if (req.method != "GET" && req.method != "HEAD") {
        return (pass);
    }

    # Normalize: sort query string for consistent cache keys
    set req.url = std.querysort(req.url);

    # Strip tracking parameters (utm_*, fbclid, gclid, etc.)
    # Copy the full regex from your production VCL or use a curated list.
    if (req.url ~ "(\?|&)(utm_|fbclid|gclid|gclsrc)") {
        set req.url = regsuball(req.url, "(utm_[a-z_]+|fbclid|gclid|gclsrc)=[-_A-z0-9+(){}%.*]+&?", "");
        set req.url = regsub(req.url, "[?|&]+$", "");
    }

    return (hash);
}

sub vcl_backend_response {
    set beresp.do_stream = true;
    set beresp.grace = 10m;

    # Don't cache non-2xx
    if (beresp.status < 200 || beresp.status >= 300) {
        set beresp.uncacheable = true;
        return (deliver);
    }

    # TTL from backend x-varnish-cache header (app controls cache duration)
    if (beresp.http.x-varnish-cache) {
        if (beresp.http.x-varnish-cache ~ "^[0-9]+$") {
            set beresp.http.x-varnish-cache = beresp.http.x-varnish-cache + "s";
        }
        set beresp.ttl = std.duration(beresp.http.x-varnish-cache, 0s);
    } else {
        set beresp.ttl = 0s;
    }
}

sub vcl_deliver {
    if (obj.hits > 0) {
        set resp.http.X-Cache = "HIT";
    } else {
        set resp.http.X-Cache = "MISS";
    }
    set resp.http.X-Cache-Hits = obj.hits;
}

sub vcl_synth {
    if (req.method == "PURGE" && resp.status == 200) {
        set resp.http.Content-Type = "text/plain; charset=utf-8";
        set resp.body = "200 Purged.";
        return (deliver);
    }
    if (req.method == "BAN" && resp.status == 200) {
        set resp.http.Content-Type = "text/plain; charset=utf-8";
        set resp.body = "200 Banned.";
        return (deliver);
    }
}
