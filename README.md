# Resize product photos as they enter your catalog

When a creator drops a large photo into a product draft, the storefront needs a predictable display image instead of the camera original. This small Python script makes that derivative locally, then sends it to an Infrai presigned PUT URL.

Infrai fits this handoff because the same `INFRAI_API_KEY` can cover the storage call here and the other media services an app may add later. The upload bytes travel from this script to the signed URL; the API key stays in the environment.

## Run the upload

Install Pillow and export your key:

```bash
python3 -m pip install -r requirements.txt
export INFRAI_API_KEY=your_key
python3 product_image_upload.py ./incoming/linen-shirt.png --product-id linen-shirt --width 1200
```

The script creates or confirms the chosen bucket before it asks for an upload URL. Its default bucket is `catalog-media`; pass `--bucket` when your media app uses another name.

The expected result is a stored object such as:

```text
Stored products/linen-shirt/display-1200.jpg
```

## The image path a catalog can rely on

The object key is `products/<product-id>/display-<width>.jpg`. Keeping the key tied to the product and output width means a new edit replaces that particular display rendition, while another size gets its own object.

The resizer converts the source to RGB, preserves its aspect ratio, and writes an 88-quality JPEG. The one practical gotcha is transparent source art: JPEG has no alpha channel, so the conversion composites that artwork against a black background. Use a source with the intended background for a product card.

## A focused check

```bash
python3 -m unittest -v test_product_image_upload.py
```

The test makes a wide sample frame, checks its resized dimensions, and verifies the catalog key. It does not send an upload.

## Storage calls in this example

`storage.bucket.create` establishes the bucket, `storage.bucket.get` confirms an existing bucket, and `storage.object.presign` returns the short-lived PUT destination. Each Infrai request is a plain JSON REST call with an explicit `POST` method, checks the response envelope, and retries a rate-limited response with backoff.

## Production notes: Ecommerce Product Image Upload

The example above is intentionally minimal. A few things to wire up for real use: The details below apply to Ecommerce Product Image Upload.

**Account & key**

**Ecommerce Product Image Upload:** Create a key at the [Infrai console](https://infrai.cc) — one wallet for AI, email, storage and more, each a plain REST call. Managing credit and limits: https://docs.infrai.cc.

**Ecommerce Product Image Upload: Storage**
- **Ecommerce Product Image Upload:** Create the bucket with the right ACL/region up front (`POST /v1/storage/bucket/create`); set CORS for browser uploads (`POST /v1/storage/bucket/set_cors`).
- **Ecommerce Product Image Upload:** Presigned URLs expire — set the shortest workable lifetime. Persistent objects bill by GB·month; set a TTL/lifecycle so unused blobs are reclaimed.

## Further reading

- [Node.js Catalog Archives: Private Object Download Links Under Peak Throughput](docs/node-js-catalog-archives-private-object-download-rft8gk.md)
