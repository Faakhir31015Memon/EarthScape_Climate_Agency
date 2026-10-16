import { afterEach, describe, expect, it } from "vitest";
import {
  ApiError,
  clearTokens,
  getAccessToken,
  getRefreshToken,
  setTokens,
  unwrapEnvelope,
} from "./client";
import type { ApiEnvelope } from "./types";

function envelope<T>(data: T): ApiEnvelope<T> {
  return { success: true, data, error: null, meta: { correlation_id: "cid-1" } };
}

describe("unwrapEnvelope", () => {
  it("unwraps a successful envelope into { data, meta }", () => {
    const result = unwrapEnvelope<{ a: number }>(envelope({ a: 1 }), 200);
    expect(result).toEqual({ data: { a: 1 }, meta: { correlation_id: "cid-1" } });
  });

  it("throws a typed ApiError for a success:false envelope", () => {
    const body = {
      success: false,
      data: null,
      error: { code: "validation_failed", message: "Bad filter", details: ["required"] },
      meta: { correlation_id: "cid-2" },
    } satisfies ApiEnvelope<null>;

    let caught: unknown;
    try {
      unwrapEnvelope(body, 422);
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(ApiError);
    const apiErr = caught as ApiError;
    expect(apiErr.code).toBe("validation_failed");
    expect(apiErr.status).toBe(422);
    expect(apiErr.details).toEqual(["required"]);
    expect(apiErr.correlationId).toBe("cid-2");
  });

  it("normalises object-shaped details into a string array", () => {
    const body = {
      success: false,
      data: null,
      error: { code: "request_failed", message: "Denied", details: { detail: "no permission" } },
      meta: { correlation_id: "cid-3" },
    } satisfies ApiEnvelope<null>;

    expect(() => unwrapEnvelope(body, 403)).toThrowError(/no permission|Denied/);
  });
});

describe("token store (rotation persistence)", () => {
  afterEach(() => clearTokens());

  it("keeps the access token in memory and the refresh token in storage", () => {
    setTokens("access-A", "refresh-A");
    expect(getAccessToken()).toBe("access-A");
    expect(getRefreshToken()).toBe("refresh-A");
  });

  it("overwrites the previous pair on rotation so a single-use refresh is never reused", () => {
    setTokens("access-1", "refresh-1");
    setTokens("access-2", "refresh-2");
    expect(getAccessToken()).toBe("access-2");
    expect(getRefreshToken()).toBe("refresh-2");
  });

  it("clears both on sign-out", () => {
    setTokens("a", "r");
    clearTokens();
    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
  });
});
