# MongoDB Atlas (I3)

One M0 cluster, one database user, network access 0.0.0.0/0 for the weekend.

- Database `nightscout`: set MONGODB_URI on the Railway Nightscout service; it restarts empty and refills from Dexcom Share. Admin Tools access tokens live in the database, so restore the old data (mongodump/mongorestore) or recreate the `irin` read token and update NIGHTSCOUT_TOKEN on the Pi. Check: /api/v1/status.json is green and the Pi's poller still gets readings.
- Database `relay` (relay/store.py): collections users, buddy_links, matches, hub_listings, hub_claims, pairings, cards, messages, resolutions, audit; TTL indexes on hub_listings.claim_expires_at and treating_expires_at. Ciphertext and profile fields only; never a glucose value (a document dump greps clean for mgdl).
- ATLAS_URI goes in the server's .env; the laptop compose uses its local `mongo:7` instead.
