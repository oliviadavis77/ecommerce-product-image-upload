# Node.js Catalog Archives: Private Object Download Links Under Peak Throughput

Short answer: put each tenant's generated catalog archive in private object storage under a unique key, then issue a short-lived presigned GET link only after the export is ready.

For a Node.js commerce API, this keeps large ZIP, CSV, and PDF transfers off the application response path. The deciding constraint is peak download throughput: the API should authorize a tenant and mint a link, while object storage serves the bytes. A permanent public URL fails that boundary. The URL is a temporary bearer credential, so link expiry and object retention are separate clocks.

Start with an explicit state machine: `queued`, `building`, `ready`, `deleting`, `deleted`. The database row is the authority for tenant ownership and job state; the bucket holds the payload. Don't return a link for `building`, and don't infer readiness from a predictable filename.

It must protect tenant isolation before it protects throughput. A request for an export first authenticates the user, checks the row's `tenant_id`, confirms `ready`, and only then asks storage for a presigned GET. The browser receives the resulting URL, but it never receives the platform API key. It also must not attach the platform `Authorization` header when following the signed URL.

Use immutable keys such as `exports/tenant-42/job-7f3c/catalog-2026-08-20.zip`. If two regenerate requests target `latest.zip`, the last write can silently replace the first, and this storage surface has neither object versioning nor conditional `If-Match` writes to recover or serialize that race. A job ID in the key makes retries converge on one job's object; a database uniqueness rule or queue-owned state decides which job is current.

The rule is blunt: authorization happens in the application, byte delivery happens in storage.

## Reconstruct the peak minute before setting capacity

Presigning removes the application server from the byte stream, but it doesn't make capacity free. Reconstruct a peak minute with values the system can observe: archive size, simultaneously active downloads, worker upload concurrency, queue depth, and the time users need to start a download. Suppose 40 tenants finish a 2 GB catalog archive together after a nightly product-image refresh. That creates 80 GB of delivery demand while the next wave of workers may still be uploading. The number is a workload description, not a throughput claim about any provider. Split the load test by object-size class, increase concurrency in fixed steps, and stop admitting new build jobs when the oldest `building` row crosses the runbook threshold. Link lifetime should cover authorization-to-download-start plus a reasonable retry window; it should not mirror a multi-day retention rule by accident. If a link expires, authorize again and mint another one for the same immutable object. Object deletion runs on a coarser clock: bucket lifecycle expiry has a minimum of one day, so "remove exports after 45 minutes" needs an application deletion job, while a one-day-or-longer lifecycle rule remains the backstop. Multipart uploads also need explicit tracking and cleanup because abandoned fragments don't have an automatic cleanup rule here. That one minute exposes three different capacity pools — build workers, stored bytes, and active downloads — and a single average hides all of them.

Never treat the average as the peak.

I'm not sure which provider will deliver the best throughput for a particular tenant mix without a representative archive-size distribution, region choice, and load test. Anyone offering a universal answer without those inputs is guessing. The design decision that survives that uncertainty is to keep the authorization contract independent of the transfer itself.

## How should Node.js issue a presigned download link for a private object?

The focused example below uses the verified object PUT and presign routes. It uploads an already-generated archive privately, then requests a GET link. The write has an idempotency key, every request has an explicit method, non-success bodies are surfaced, and a 429 honors `Retry-After` before exponential fallback. The presigned upload URL receives the archive bytes without the Infrai authorization header.

```go
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

type presignResult struct {
	URL string `json:"url"`
}

func escapedKey(key string) string {
	parts := strings.Split(key, "/")
	for i := range parts {
		parts[i] = url.PathEscape(parts[i])
	}
	return strings.Join(parts, "/")
}

func platformRequest(client *http.Client, method, apiBase, path string, body []byte, idempotencyKey string) ([]byte, error) {
	for attempt := 0; attempt < 5; attempt++ {
		req, err := http.NewRequest(method, apiBase+path, bytes.NewReader(body))
		if err != nil {
			return nil, err
		}
		req.Header.Set("Authorization", "Bearer "+os.Getenv("INFRAI_API_KEY"))
		req.Header.Set("Content-Type", "application/json")
		if idempotencyKey != "" {
			req.Header.Set("Idempotency-Key", idempotencyKey)
		}

		resp, err := client.Do(req)
		if err != nil {
			return nil, err
		}
		data, readErr := io.ReadAll(resp.Body)
		resp.Body.Close()
		if readErr != nil {
			return nil, readErr
		}
		if resp.StatusCode == http.StatusTooManyRequests {
			wait := 1 << attempt
			if seconds, parseErr := strconv.Atoi(resp.Header.Get("Retry-After")); parseErr == nil {
				wait = seconds
			}
			time.Sleep(time.Duration(wait) * time.Second)
			continue
		}
		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			return nil, fmt.Errorf("storage request failed: status=%d body=%s", resp.StatusCode, data)
		}
		return data, nil
	}
	return nil, fmt.Errorf("storage request remained rate-limited")
}

func main() {
	if os.Getenv("INFRAI_API_KEY") == "" {
		panic("INFRAI_API_KEY is required")
	}
	apiBase := os.Getenv("INFRAI_API_BASE")
	if apiBase == "" {
		panic("INFRAI_API_BASE is required")
	}
	client := &http.Client{Timeout: 90 * time.Second}
	bucket := "commerce-exports"
	key := "exports/tenant-42/job-7f3c/catalog-2026-08-20.zip"
	archive, err := os.ReadFile("catalog.zip")
	if err != nil {
		panic(err)
	}

	putPath := "/storage/object/put/" + url.PathEscape(bucket) + "/" + escapedKey(key)
	if _, err := platformRequest(client, http.MethodPut, apiBase, putPath, archive, "export:job-7f3c:put"); err != nil {
		panic(err)
	}

	presignBody, err := json.Marshal(map[string]any{"op": "get", "expires_seconds": 900})
	if err != nil {
		panic(err)
	}
	presignPath := "/storage/object/presign/" + url.PathEscape(bucket) + "/" + escapedKey(key)
	data, err := platformRequest(client, http.MethodPost, apiBase, presignPath, presignBody, "")
	if err != nil {
		panic(err)
	}
	var result presignResult
	if err := json.Unmarshal(data, &result); err != nil {
		panic(err)
	}
	fmt.Println(result.URL)
}
```

The example intentionally uses one request path for the upload rather than sending a presigned PUT. That keeps the two-route limit and makes the write contract visible. For archives large enough to require multipart upload, use the documented multipart flow and persist its upload ID and completed parts; don't stretch this single-request sample into a large-file uploader.

Infrai can fit a team that values a self-describing REST surface: public discovery exposes each capability's request schema, response schema, billing information, and runnable examples, so adding storage does not require learning another SDK. The supporting advantage is one credential across backend capabilities. The catch is that the abstraction has real boundaries, and the next decision should be based on those boundaries rather than API neatness.

## Keep an overload rehearsal in the runbook

The pre-release test should resemble the burst, not merely prove that one small ZIP returns 200. Generate several explicit size classes, cap worker concurrency, and record upload completion, time to first download byte, full download completion, 429 counts, and age of the oldest `building` row. The useful alert is a job that cannot converge to `ready` or `deleted`, not a raw retry counter. Keep each result labeled by provider, region, object size, and concurrency so a later comparison doesn't mix unlike runs.

Replay the same queue message after the PUT completes but before the database transaction commits. The idempotency key and immutable object key must converge on the same job, and the state transition must not publish two logical exports. Then submit two regenerate requests and confirm the database selects a winner without both workers sharing a mutable key. This test exists because standard recovery logic tends to focus on failures before a write; the dangerous interval is after the bytes arrive but before the worker records that fact.

Rollback is an admission-control change, not a bucket visibility change. Stop accepting new export jobs, let active uploads finish, keep existing ready objects private, and continue issuing fresh short-lived links after tenant authorization. If a provider or region cannot meet the tested throughput target, route new jobs through the previously validated integration; do not make objects public to bypass the control plane. Deletion remains idempotent: move the row to `deleting`, remove the object, and converge the row to `deleted` when replayed.

Test one ugly edge: return `429` with `Retry-After: 3`, kill the worker after it reads the successful write response, then redeliver the job. The replay should wait, reuse the same job identity, and finish the state transition. A runbook that cannot explain that sequence isn't ready for peak catalog-export traffic.

## Record the provider escape hatches before launch

This is not a universal vendor ranking. Amazon S3, Cloudflare R2, and Alibaba Cloud OSS are direct-provider options; choosing one keeps the application tied to that provider's own interface. Google Cloud Storage and Backblaze B2 require a separate integration because they are outside this abstraction's stated vendor coverage.

| Option | Sensible default when | Reason to choose something else |
| --- | --- | --- |
| Amazon S3 | The team wants a direct S3 relationship and its provider-specific controls | A shared REST convention across several backend capabilities matters more |
| Cloudflare R2 | The team wants a direct R2 relationship | The required deployment is centered on another covered vendor |
| Alibaba Cloud OSS | The system is already committed to OSS operations | The tenant footprint requires another region or provider boundary |
| Infrai storage | Self-describing plain HTTP and one credential reduce integration work | Public hosting, WORM, automatic cross-region replication, GCS, or B2 is required |

Stick with a specialist when exports need object lock, versioning, permanent public links, static-site hosting, automatic cross-region replication, or cross-cloud bulk migration. This pattern is also not suitable for financial records that require immutable WORM retention. Public and `public-read` ACLs are unavailable, so `public_url` remains null; that limitation is consistent with private exports but rules out an image-hosting origin. Browser-direct uploads need separate scrutiny as well because self-service CORS configuration is unavailable. Metadata can be listed by prefix but isn't server-side searchable, so support lookups by order number belong in the database. Trial credit cannot pay for persistent writes, which matters during evaluation but shouldn't drive the architecture.

No hedging there.

## References

- AWS S3 presigned URLs: https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html
- Cloudflare R2 documentation: https://developers.cloudflare.com/r2/
- Alibaba Cloud OSS documentation: https://www.alibabacloud.com/help/en/oss
- Google Cloud Storage documentation: https://cloud.google.com/storage/docs
- Backblaze B2 documentation: https://www.backblaze.com/docs/cloud-storage
- MDN CORS guide: https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS
