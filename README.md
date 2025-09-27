# rearvue

A personal social media aggregator / nostalgia engine.

Collates your posts from around the web and brings them home.

Very, very rough and ready.

## Recent Updates

- **Instagram Service**: Updated to use the official Instagram Graph API (see [INSTAGRAM_MIGRATION.md](INSTAGRAM_MIGRATION.md) for details)

## "Supported" Services

|Service   |Imports archive|Live Updates|
|----------|:-------------:|:----------:|
|Twitter   | ✅            | ❌         |
|Flickr    | ✅            | ✅         |
|RSS       | ✅ (*)        | ✅         |
|Instagram | ✅            | ✅         |

**Note**: Instagram integration requires a Business or Creator account and uses the official Instagram Graph API.

Services that can be imported using RSS

- Github
- Mastodon


_*_ Can only import full archives if the source feed supports pagination.
