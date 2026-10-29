import type { ReactNode } from "react";

import {
  configureChannel,
  disableYoreniFeature,
  enableYoreniFeature,
} from "@/lib/api";
import type { YoreniChannelInstanceInfo, YoreniFeatureInfo, YoreniFeaturesPayload } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";
import {
  ChannelInstancesPanelView,
  type ChannelInstancesPanelCustomization,
  type ChannelInstancesPanelViewProps,
} from "./ChannelInstancesPanelView";

export type { ChannelInstancesPanelCustomization } from "./ChannelInstancesPanelView";

export function ChannelInstancesPanel({
  feature,
  showBrandLogos,
  instances,
  onFeaturesUpdate,
  customization,
  renderView,
}: {
  feature: YoreniFeatureInfo;
  showBrandLogos: boolean;
  instances?: YoreniChannelInstanceInfo[];
  onFeaturesUpdate: (payload: YoreniFeaturesPayload) => void;
  customization?: ChannelInstancesPanelCustomization;
  renderView?: (props: ChannelInstancesPanelViewProps) => ReactNode;
}) {
  const { client } = useClient();
  const viewProps: ChannelInstancesPanelViewProps = {
    feature,
    showBrandLogos,
    instances,
    customization,
    onToggleInstance: async (instance, checked) => {
      const payload = checked
        ? await enableYoreniFeature(client, feature.name, { instanceId: instance.id })
        : await disableYoreniFeature(client, feature.name, { instanceId: instance.id });
      onFeaturesUpdate(payload);
    },
    onSaveInstance: async (instance, values) => {
      const payload = await configureChannel(client, feature.name, values, {
        enable: instance.enabled,
        instanceId: instance.id,
      });
      if (payload.yoreni_features) onFeaturesUpdate(payload.yoreni_features);
    },
  };
  return renderView ? renderView(viewProps) : <ChannelInstancesPanelView {...viewProps} />;
}
