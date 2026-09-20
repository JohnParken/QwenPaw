import { Form, Input, InputNumber, Switch } from "@agentscope-ai/design";
import { useTranslation } from "react-i18next";
import type { TLConfig } from "../../../../../api/types";

export function TLConfigFields({ defaults }: { defaults: TLConfig }) {
  const { t } = useTranslation();
  const textFields: Array<[keyof TLConfig, string]> = [
    ["app_id", "models.tlAppId"],
    ["tr_code", "models.tlTrCode"],
    ["tr_version", "models.tlTrVersion"],
    ["system_prompt_variable_name", "models.tlSystemPromptVariable"],
  ];
  const budgetFields: Array<[keyof TLConfig, string]> = [
    ["json_correction_max_attempts", "models.tlJsonCorrectionAttempts"],
    ["timeout_seconds", "models.tlTimeoutSeconds"],
    ["stream_idle_timeout_seconds", "models.tlStreamIdleTimeoutSeconds"],
    ["max_request_bytes", "models.tlMaxRequestBytes"],
    ["max_response_bytes", "models.tlMaxResponseBytes"],
    ["max_wire_response_bytes", "models.tlMaxWireResponseBytes"],
    ["max_sse_event_bytes", "models.tlMaxSseEventBytes"],
  ];

  return (
    <>
      <div
        style={{ marginBottom: 16, color: "var(--ant-color-text-secondary)" }}
      >
        {t("models.tlDescription")}
      </div>
      {textFields.map(([name, label]) => (
        <Form.Item
          key={name}
          name={["tl_config", name]}
          label={t(label)}
          rules={
            name === "system_prompt_variable_name"
              ? [{ required: true, whitespace: true }]
              : undefined
          }
          initialValue={defaults[name]}
        >
          <Input />
        </Form.Item>
      ))}
      <Form.Item
        name={["tl_config", "tool_calling_mode"]}
        label={t("models.tlToolCallingMode")}
        initialValue={defaults.tool_calling_mode}
      >
        <Input disabled />
      </Form.Item>
      <Form.Item
        name={["tl_config", "trust_env"]}
        label={t("models.tlTrustEnv")}
        extra={t("models.tlTrustEnvHint")}
        valuePropName="checked"
        initialValue={defaults.trust_env}
      >
        <Switch />
      </Form.Item>
      {budgetFields.map(([name, label]) => (
        <Form.Item
          key={name}
          name={["tl_config", name]}
          label={t(label)}
          rules={[
            {
              required: true,
              type: "number",
              min:
                name === "stream_idle_timeout_seconds" ||
                name === "json_correction_max_attempts"
                  ? 0
                  : 1,
              max: name === "json_correction_max_attempts" ? 1 : undefined,
            },
          ]}
          initialValue={defaults[name]}
        >
          <InputNumber
            min={
              name === "stream_idle_timeout_seconds" ||
              name === "json_correction_max_attempts"
                ? 0
                : 1
            }
            max={name === "json_correction_max_attempts" ? 1 : undefined}
            precision={0}
            style={{ width: "100%" }}
          />
        </Form.Item>
      ))}
    </>
  );
}
