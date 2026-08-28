# Phase 2 — Multi-format Metadata Adapters

Mnemosyne's first complete acquisition proved the transaction model with M4A. The metadata layer is now format-aware.

Supported families:
- MP4/M4A/M4B: canonical MP4 atoms; JPEG/PNG front cover.
- MP3: ID3v2.4 TIT2/TPE1/TPE2/TALB/TDRC/TCON; APIC type 3 front cover.
- FLAC: Vorbis comments title/artist/albumartist/album/date/genre; FLAC PICTURE type 3 front cover.

MP3 and FLAC support JPEG, PNG, and WebP artwork. Unrelated metadata and non-front artwork are preserved where practical.

Tagging, inspection, and readiness now share the same adapter and verification layer. The transactional contract remains: work on a temporary copy, verify tags/artwork, preserve rollback, atomically replace staged media, verify again, then update provenance.
