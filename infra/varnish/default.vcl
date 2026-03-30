# Varnish 7.x + VCL 4.1: backend .port is a string; vcl_recv uses return (hash) — not (lookup).
vcl 4.1;

backend default {
  .host = "cache-nginx";
  .port = "80";
}

sub vcl_recv {
  return (hash);
}

sub vcl_backend_response {
  set beresp.ttl = 60s;
}
