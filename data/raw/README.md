# Raw provider responses

The pipeline writes immutable JSON envelopes here as:
`<provider>/<YYYY-MM-DD>/<request-id>.json`. Payloads and request metadata are stored with a SHA-256
hash; secrets and authorization headers are excluded. Generated envelopes are intentionally
ignored by Git.
