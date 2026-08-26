# Changelog

## Unreleased

### Added
- Shared format-aware metadata adapter layer for MP4, MP3/ID3, and FLAC.
- MP3 ID3v2.4 canonical metadata and APIC front-cover writing/verification.
- FLAC Vorbis-comment metadata and PICTURE front-cover writing/verification.
- Multi-format canonical inspection and artwork SHA-256 readiness verification.

### Changed
- Transactional tagging and staged readiness are no longer MP4-specific.
- Metadata provenance records the metadata family.
