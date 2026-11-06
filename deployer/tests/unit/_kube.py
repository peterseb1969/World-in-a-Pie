"""The cluster unit tests' saved k8s specs record (see conftest's
_no_live_cluster, whose cluster check answers with UNIT_CLUSTER_UID)."""

from wip_deploy.spec.deployment import K8sCluster

UNIT_CLUSTER_UID = "unit-test-cluster"


def unit_cluster() -> K8sCluster:
    return K8sCluster(
        kubeconfig="/unit-test/kubeconfig", context="unit-test", cluster_uid=UNIT_CLUSTER_UID,
    )
