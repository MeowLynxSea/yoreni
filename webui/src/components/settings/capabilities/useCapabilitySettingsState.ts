import { useState } from "react";

import {
  DEFAULT_IMAGE_GENERATION_FORM,
  imageGenerationFormFromPayload,
} from "@/components/settings/capabilities/ImageGenerationSettings";
import {
  DEFAULT_MEMORY_FORM,
  memoryFormFromPayload,
} from "@/components/settings/capabilities/MemorySettings";
import {
  DEFAULT_TRANSCRIPTION_FORM,
  transcriptionFormFromPayload,
} from "@/components/settings/capabilities/TranscriptionSettings";
import {
  DEFAULT_WEB_SEARCH_FORM,
  webSearchFormFromPayload,
} from "@/components/settings/capabilities/WebSettings";
import type {
  ImageGenerationSettingsUpdate,
  MemorySettingsUpdate,
  SettingsPayload,
  TranscriptionSettingsUpdate,
  WebSearchSettingsUpdate,
} from "@/lib/types";

export function useCapabilitySettingsState(initialSettings: SettingsPayload | null) {
  const [capabilityErrors, setCapabilityErrors] = useState<Partial<Record<"image" | "voice" | "web" | "memory", string>>>({});
  const [webSearchSaving, setWebSearchSaving] = useState(false);
  const [imageGenerationSaving, setImageGenerationSaving] = useState(false);
  const [transcriptionSaving, setTranscriptionSaving] = useState(false);
  const [memorySaving, setMemorySaving] = useState(false);
  const [webSearchForm, setWebSearchForm] = useState<WebSearchSettingsUpdate>(() =>
    initialSettings ? webSearchFormFromPayload(initialSettings) : DEFAULT_WEB_SEARCH_FORM,
  );
  const [imageGenerationForm, setImageGenerationForm] = useState<ImageGenerationSettingsUpdate>(
    () => initialSettings
      ? imageGenerationFormFromPayload(initialSettings)
      : DEFAULT_IMAGE_GENERATION_FORM,
  );
  const [transcriptionForm, setTranscriptionForm] = useState<TranscriptionSettingsUpdate>(
    () => initialSettings ? transcriptionFormFromPayload(initialSettings) : DEFAULT_TRANSCRIPTION_FORM,
  );
  const [memoryForm, setMemoryForm] = useState<MemorySettingsUpdate>(
    () => initialSettings ? memoryFormFromPayload(initialSettings) : DEFAULT_MEMORY_FORM,
  );
  const [webSearchKeyVisible, setWebSearchKeyVisible] = useState(false);
  const [webSearchKeyEditing, setWebSearchKeyEditing] = useState(false);

  return {
    capabilityErrors,
    setCapabilityErrors,
    imageGenerationForm,
    imageGenerationSaving,
    memoryForm,
    memorySaving,
    setImageGenerationForm,
    setImageGenerationSaving,
    setMemoryForm,
    setMemorySaving,
    setTranscriptionForm,
    setTranscriptionSaving,
    setWebSearchForm,
    setWebSearchKeyEditing,
    setWebSearchKeyVisible,
    setWebSearchSaving,
    transcriptionForm,
    transcriptionSaving,
    webSearchForm,
    webSearchKeyEditing,
    webSearchKeyVisible,
    webSearchSaving,
  };
}

export type CapabilitySettingsState = ReturnType<typeof useCapabilitySettingsState>;
