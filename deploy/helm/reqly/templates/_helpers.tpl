{{/* Names */}}
{{- define "reqly.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "reqly.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else if contains (include "reqly.name" .) .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "reqly.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/* Labels; pass (dict "ctx" $ "component" "collector") */}}
{{- define "reqly.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .ctx.Chart.Name .ctx.Chart.Version | replace "+" "_" }}
{{ include "reqly.selectorLabels" . }}
app.kubernetes.io/version: {{ .ctx.Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .ctx.Release.Service }}
{{- end -}}

{{- define "reqly.selectorLabels" -}}
app.kubernetes.io/name: {{ include "reqly.name" .ctx }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "reqly.secretName" -}}
{{- default (printf "%s-secrets" (include "reqly.fullname" .)) .Values.secrets.existingSecret -}}
{{- end -}}

{{- define "reqly.timescaledbHost" -}}
{{- printf "%s-timescaledb" (include "reqly.fullname" .) -}}
{{- end -}}

{{/* image: pass (dict "image" .Values.collector.image "ctx" $) */}}
{{- define "reqly.image" -}}
{{- printf "%s:%s" .image.repository (default .ctx.Chart.AppVersion .image.tag) -}}
{{- end -}}

{{/*
A value that is generated once and kept: the explicit value if set, else
what the existing Secret holds, else a new random string.
Pass (dict "ctx" $ "key" "ingest-key" "value" .Values.secrets.ingestKey "length" 32)
*/}}
{{- define "reqly.persisted" -}}
{{- if .value -}}
{{- .value -}}
{{- else -}}
{{- $existing := lookup "v1" "Secret" .ctx.Release.Namespace (include "reqly.secretName" .ctx) -}}
{{- if and $existing (index $existing.data .key) -}}
{{- index $existing.data .key | b64dec -}}
{{- else -}}
{{- randAlphaNum (int .length) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* The browser-facing collector URL the dashboard calls. */}}
{{- define "reqly.publicCollectorUrl" -}}
{{- if .Values.dashboard.collectorUrl -}}
{{- .Values.dashboard.collectorUrl -}}
{{- else if .Values.ingress.enabled -}}
{{- $tls := gt (len .Values.ingress.tls) 0 -}}
{{- printf "%s://%s" (ternary "https" "http" $tls) .Values.ingress.collector.host -}}
{{- else -}}
http://localhost:8000
{{- end -}}
{{- end -}}
