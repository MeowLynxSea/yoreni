import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { CredentialAuthRequest } from "@/lib/types";
import { cn } from "@/lib/utils";

const NON_SECRET_FIELDS = new Set(["username", "user", "email", "label"]);

function humanize(field: string): string {
  return field
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function AuthenticationCard({
  request,
  busy,
  onCancel,
  onSubmit,
  onOAuth,
}: {
  request: CredentialAuthRequest;
  busy: boolean;
  onCancel: (requestId: string) => void;
  onSubmit: (requestId: string, values: Record<string, string>) => void;
  onOAuth: (request: CredentialAuthRequest) => void;
}) {
  const { t } = useTranslation();
  const [values, setValues] = useState<Record<string, string>>({});
  const isOAuth = request.method === "oauth2";
  const fields = request.fields.length ? request.fields : ["api_key"];

  return (
    <form
      className={cn(
        "mb-3 rounded-control border border-border/70 bg-card/80 p-3 text-xs",
        "animate-in fade-in-0 slide-in-from-bottom-1 motion-reduce:animate-none",
      )}
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit(request.id, values);
      }}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-medium text-foreground">{t("auth.title")}</p>
          <p className="mt-0.5 text-muted-foreground">{request.purpose || request.connector_id}</p>
        </div>
        <button
          type="button"
          className="text-[11px] text-muted-foreground underline-offset-2 hover:underline"
          onClick={() => onCancel(request.id)}
          disabled={busy}
        >
          {t("auth.cancel")}
        </button>
      </div>
      {isOAuth ? (
        <button
          type="button"
          className="mt-3 w-full rounded-control bg-primary px-3 py-2 text-[12px] font-medium text-primary-foreground disabled:opacity-60"
          onClick={() => onOAuth(request)}
          disabled={busy}
        >
          {t("auth.continueInBrowser")}
        </button>
      ) : (
        <div className="mt-3 grid gap-2">
          {fields.map((field) => (
            <label key={field} className="grid gap-1">
              <span className="text-[11px] text-muted-foreground">{humanize(field)}</span>
              <input
                className="h-8 rounded-control border border-border/70 bg-background px-2 text-[12px] text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
                type={NON_SECRET_FIELDS.has(field) ? "text" : "password"}
                autoComplete="off"
                value={values[field] ?? ""}
                onChange={(event) =>
                  setValues((current) => ({ ...current, [field]: event.target.value }))
                }
              />
            </label>
          ))}
          <button
            type="submit"
            className="mt-1 w-full rounded-control bg-primary px-3 py-2 text-[12px] font-medium text-primary-foreground disabled:opacity-60"
            disabled={busy}
          >
            {t("auth.connect")}
          </button>
        </div>
      )}
      {request.allowed_tools?.length || request.allowed_domains?.length ? (
        <p className="mt-2 text-[11px] text-muted-foreground">
          {[request.allowed_tools?.join(", "), request.allowed_domains?.join(", ")]
            .filter(Boolean)
            .join(" · ")}
        </p>
      ) : null}
      <p className="mt-2 text-[11px] text-muted-foreground">{t("auth.secureNote")}</p>
    </form>
  );
}
