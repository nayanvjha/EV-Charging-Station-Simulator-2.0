from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

import networkx as nx

logger = logging.getLogger("knowledge_graph")


class SecurityKnowledgeGraph:
    """In-memory, thread-safe EV security knowledge graph."""

    NODE_CHARGE_POINT = "ChargePoint"
    NODE_USER_IDTAG = "User_IdTag"
    NODE_TRANSACTION = "Transaction"
    NODE_SECURITY_EVENT = "SecurityEvent"

    EDGE_INITIATED_BY = "INITIATED_BY"
    EDGE_OCCURRED_AT = "OCCURRED_AT"
    EDGE_PART_OF_TRANSACTION = "PART_OF_TRANSACTION"
    EDGE_TARGETS = "TARGETS"

    def __init__(
        self,
        *,
        auth_window_seconds: int = 300,
        idtag_chargepoint_threshold: int = 5,
        event_fanout_window_seconds: int = 30,
        event_fanout_threshold: int = 5,
        node_ttl_seconds: int = 24 * 60 * 60,
    ) -> None:
        self.graph = nx.DiGraph()
        self._lock = Lock()

        self.auth_window_seconds = max(1, int(auth_window_seconds))
        self.idtag_chargepoint_threshold = max(1, int(idtag_chargepoint_threshold))
        self.event_fanout_window_seconds = max(1, int(event_fanout_window_seconds))
        self.event_fanout_threshold = max(1, int(event_fanout_threshold))
        self.node_ttl_seconds = max(60, int(node_ttl_seconds))

        self._idtag_auth_index: Dict[str, Deque[Tuple[float, str]]] = defaultdict(deque)
        self._event_index: Dict[str, Deque[Tuple[float, str, str]]] = defaultdict(deque)
        self._tx_counter = 0
        self._event_counter = 0

    @staticmethod
    def _now(timestamp: Optional[float] = None) -> float:
        return float(timestamp) if timestamp is not None else time.time()

    @staticmethod
    def _node_id(node_type: str, node_key: str) -> str:
        return f"{node_type}:{node_key}"

    @staticmethod
    def _edge_key(src: str, dst: str, relation: str) -> Tuple[str, str, str]:
        return src, dst, relation

    def _ensure_node(self, node_id: str, node_type: str, **attrs: Any) -> None:
        if self.graph.has_node(node_id):
            self.graph.nodes[node_id].update(attrs)
            return
        self.graph.add_node(node_id, type=node_type, **attrs)

    def _ensure_edge(self, src: str, dst: str, relation: str, **attrs: Any) -> None:
        self.graph.add_edge(src, dst, relation=relation, **attrs)

    def add_transaction(
        self,
        *,
        charge_point_id: str,
        transaction_id: str,
        id_tag: Optional[str] = None,
        energy_delivered: Optional[float] = None,
        charging_duration: Optional[float] = None,
        average_power: Optional[float] = None,
        timestamp: Optional[float] = None,
    ) -> str:
        now = self._now(timestamp)
        cp = str(charge_point_id)
        tx = str(transaction_id)
        tx_node = self._node_id(self.NODE_TRANSACTION, tx)
        cp_node = self._node_id(self.NODE_CHARGE_POINT, cp)

        with self._lock:
            self._ensure_node(cp_node, self.NODE_CHARGE_POINT, charge_point_id=cp, last_seen=now)
            self._ensure_node(
                tx_node,
                self.NODE_TRANSACTION,
                transaction_id=tx,
                timestamp=now,
                energy_delivered=energy_delivered,
                charging_duration=charging_duration,
                average_power=average_power,
            )
            self._ensure_edge(tx_node, cp_node, self.EDGE_OCCURRED_AT, timestamp=now)

            if id_tag:
                user = str(id_tag)
                user_node = self._node_id(self.NODE_USER_IDTAG, user)
                self._ensure_node(user_node, self.NODE_USER_IDTAG, id_tag=user, last_seen=now)
                self._ensure_edge(tx_node, user_node, self.EDGE_INITIATED_BY, timestamp=now)
                self._ensure_edge(user_node, cp_node, self.EDGE_OCCURRED_AT, timestamp=now)

            logger.info(
                json.dumps(
                    {
                        "event": "KG_ADD_TRANSACTION",
                        "transaction_id": tx,
                        "charge_point_id": cp,
                        "id_tag": id_tag,
                    }
                )
            )
            return tx_node

    def add_security_event(
        self,
        charge_point_id: str,
        event_type: str,
        severity: int,
        description: str,
        *,
        transaction_id: Optional[str] = None,
        id_tag: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> str:
        now = self._now(timestamp)
        cp = str(charge_point_id)
        ev_type = str(event_type)
        cp_node = self._node_id(self.NODE_CHARGE_POINT, cp)

        with self._lock:
            self._event_counter += 1
            event_id = f"evt_{int(now * 1000)}_{self._event_counter}"
            ev_node = self._node_id(self.NODE_SECURITY_EVENT, event_id)

            self._ensure_node(cp_node, self.NODE_CHARGE_POINT, charge_point_id=cp, last_seen=now)
            self._ensure_node(
                ev_node,
                self.NODE_SECURITY_EVENT,
                event_id=event_id,
                event_type=ev_type,
                severity=int(severity),
                description=str(description),
                timestamp=now,
            )
            self._ensure_edge(ev_node, cp_node, self.EDGE_TARGETS, timestamp=now)

            if transaction_id:
                tx_node = self._node_id(self.NODE_TRANSACTION, str(transaction_id))
                self._ensure_node(tx_node, self.NODE_TRANSACTION, transaction_id=str(transaction_id), timestamp=now)
                self._ensure_edge(ev_node, tx_node, self.EDGE_PART_OF_TRANSACTION, timestamp=now)

            if id_tag:
                user_node = self._node_id(self.NODE_USER_IDTAG, str(id_tag))
                self._ensure_node(user_node, self.NODE_USER_IDTAG, id_tag=str(id_tag), last_seen=now)
                self._ensure_edge(ev_node, user_node, self.EDGE_INITIATED_BY, timestamp=now)

            self._event_index[ev_type].append((now, cp, ev_node))
            self._prune_event_index_locked(ev_type, now)

            logger.info(
                json.dumps(
                    {
                        "event": "KG_ADD_SECURITY_EVENT",
                        "event_type": ev_type,
                        "charge_point_id": cp,
                        "severity": int(severity),
                        "event_node": ev_node,
                    }
                )
            )
            return ev_node

    def link_user_chargepoint(
        self,
        id_tag: str,
        charge_point_id: str,
        *,
        timestamp: Optional[float] = None,
        relation: str = EDGE_INITIATED_BY,
    ) -> None:
        now = self._now(timestamp)
        user = str(id_tag)
        cp = str(charge_point_id)
        user_node = self._node_id(self.NODE_USER_IDTAG, user)
        cp_node = self._node_id(self.NODE_CHARGE_POINT, cp)

        with self._lock:
            self._ensure_node(user_node, self.NODE_USER_IDTAG, id_tag=user, last_seen=now)
            self._ensure_node(cp_node, self.NODE_CHARGE_POINT, charge_point_id=cp, last_seen=now)
            self._ensure_edge(user_node, cp_node, relation, timestamp=now)

            self._idtag_auth_index[user].append((now, cp))
            self._prune_idtag_index_locked(user, now)

            logger.info(
                json.dumps(
                    {
                        "event": "KG_LINK_USER_CHARGEPOINT",
                        "id_tag": user,
                        "charge_point_id": cp,
                        "relation": relation,
                    }
                )
            )

    def _prune_idtag_index_locked(self, id_tag: str, now: float) -> None:
        dq = self._idtag_auth_index.get(id_tag)
        if dq is None:
            return
        cutoff = now - self.auth_window_seconds
        while dq and dq[0][0] < cutoff:
            dq.popleft()
        if not dq:
            self._idtag_auth_index.pop(id_tag, None)

    def _prune_event_index_locked(self, event_type: str, now: float) -> None:
        dq = self._event_index.get(event_type)
        if dq is None:
            return
        cutoff = now - self.event_fanout_window_seconds
        while dq and dq[0][0] < cutoff:
            dq.popleft()
        if not dq:
            self._event_index.pop(event_type, None)

    def detect_coordinated_threats(
        self,
        *,
        auth_window_seconds: Optional[int] = None,
        idtag_chargepoint_threshold: Optional[int] = None,
        event_window_seconds: Optional[int] = None,
        event_chargepoint_threshold: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        auth_window = self.auth_window_seconds if auth_window_seconds is None else max(1, int(auth_window_seconds))
        idtag_threshold = (
            self.idtag_chargepoint_threshold
            if idtag_chargepoint_threshold is None
            else max(1, int(idtag_chargepoint_threshold))
        )
        event_window = (
            self.event_fanout_window_seconds if event_window_seconds is None else max(1, int(event_window_seconds))
        )
        event_threshold = (
            self.event_fanout_threshold
            if event_chargepoint_threshold is None
            else max(1, int(event_chargepoint_threshold))
        )

        now = time.time()
        alerts: List[Dict[str, Any]] = []

        with self._lock:
            for id_tag in list(self._idtag_auth_index.keys()):
                dq = self._idtag_auth_index.get(id_tag)
                if dq is None:
                    continue
                cutoff = now - auth_window
                while dq and dq[0][0] < cutoff:
                    dq.popleft()
                if not dq:
                    self._idtag_auth_index.pop(id_tag, None)
                    continue

                cps: Set[str] = {cp for _, cp in dq}
                if len(cps) > idtag_threshold:
                    alerts.append(
                        {
                            "threat_type": "COORDINATED_AUTH_ABUSE",
                            "id_tag": id_tag,
                            "charge_points": sorted(cps),
                            "charge_point_count": len(cps),
                            "window_seconds": auth_window,
                            "severity": 9,
                            "description": (
                                f"IdTag {id_tag} triggered auth activity across {len(cps)} charge points "
                                f"within {auth_window} seconds"
                            ),
                        }
                    )

            for event_type in list(self._event_index.keys()):
                dq = self._event_index.get(event_type)
                if dq is None:
                    continue
                cutoff = now - event_window
                while dq and dq[0][0] < cutoff:
                    dq.popleft()
                if not dq:
                    self._event_index.pop(event_type, None)
                    continue

                cps = {cp for _, cp, _ in dq}
                if len(cps) > event_threshold:
                    alerts.append(
                        {
                            "threat_type": "COORDINATED_ATTACK_DETECTED",
                            "event_type": event_type,
                            "charge_points": sorted(cps),
                            "charge_point_count": len(cps),
                            "window_seconds": event_window,
                            "severity": 9,
                            "description": (
                                f"Event {event_type} observed across {len(cps)} charge points "
                                f"within {event_window} seconds"
                            ),
                        }
                    )

        if alerts:
            logger.warning(json.dumps({"event": "KG_COORDINATED_THREATS", "count": len(alerts), "alerts": alerts}))
        return alerts

    def compute_node_risk(self, node_id: str) -> float:
        with self._lock:
            if not self.graph.has_node(node_id):
                return 0.0

            node = self.graph.nodes[node_id]
            node_type = str(node.get("type", ""))
            base_risk = float(node.get("risk", 0.0))

            event_severity_sum = 0.0
            if node_type == self.NODE_SECURITY_EVENT:
                event_severity_sum += float(node.get("severity", 0))
            else:
                for predecessor in self.graph.predecessors(node_id):
                    pred = self.graph.nodes[predecessor]
                    if pred.get("type") == self.NODE_SECURITY_EVENT:
                        event_severity_sum += float(pred.get("severity", 0))

                for successor in self.graph.successors(node_id):
                    succ = self.graph.nodes[successor]
                    if succ.get("type") == self.NODE_SECURITY_EVENT:
                        event_severity_sum += float(succ.get("severity", 0))

            degree_factor = float(self.graph.in_degree(node_id) + self.graph.out_degree(node_id))
            risk = min(10.0, max(0.0, base_risk + (0.5 * event_severity_sum) + (0.1 * degree_factor)))
            self.graph.nodes[node_id]["risk"] = risk
            return risk

    def propagate_risk(
        self,
        *,
        seeds: Optional[List[str]] = None,
        depth: int = 2,
        decay: float = 0.6,
    ) -> Dict[str, float]:
        depth = max(1, int(depth))
        decay = max(0.0, min(1.0, float(decay)))

        with self._lock:
            all_nodes = list(self.graph.nodes())
            for node_id in all_nodes:
                self.compute_node_risk(node_id)

            frontier = list(seeds) if seeds else all_nodes
            propagated: Dict[str, float] = {}

            for seed in frontier:
                if not self.graph.has_node(seed):
                    continue
                seed_risk = float(self.graph.nodes[seed].get("risk", 0.0))
                if seed_risk <= 0:
                    continue

                visited = {seed}
                level = [(seed, seed_risk, 0)]
                while level:
                    current, risk_value, hop = level.pop()
                    propagated[current] = max(propagated.get(current, 0.0), risk_value)
                    if hop >= depth:
                        continue

                    next_risk = risk_value * decay
                    if next_risk <= 0:
                        continue

                    neighbors = set(self.graph.predecessors(current)) | set(self.graph.successors(current))
                    for neighbor in neighbors:
                        if neighbor in visited:
                            continue
                        visited.add(neighbor)
                        existing = float(self.graph.nodes[neighbor].get("risk", 0.0))
                        updated = min(10.0, max(existing, next_risk))
                        self.graph.nodes[neighbor]["risk"] = updated
                        level.append((neighbor, updated, hop + 1))

            logger.info(
                json.dumps(
                    {
                        "event": "KG_PROPAGATE_RISK",
                        "seed_count": len(frontier),
                        "depth": depth,
                        "decay": decay,
                        "updated_nodes": len(propagated),
                    }
                )
            )
            return propagated

    def prune_old_nodes(self, *, older_than_seconds: Optional[int] = None, now: Optional[float] = None) -> int:
        cutoff_window = self.node_ttl_seconds if older_than_seconds is None else max(1, int(older_than_seconds))
        current_time = self._now(now)
        cutoff = current_time - cutoff_window

        removed = 0
        with self._lock:
            nodes_to_remove: List[str] = []
            for node_id, attrs in self.graph.nodes(data=True):
                node_type = attrs.get("type")
                if node_type not in {self.NODE_TRANSACTION, self.NODE_SECURITY_EVENT}:
                    continue
                ts = attrs.get("timestamp")
                if isinstance(ts, (int, float)) and float(ts) < cutoff:
                    nodes_to_remove.append(node_id)

            for node_id in nodes_to_remove:
                if self.graph.has_node(node_id):
                    self.graph.remove_node(node_id)
                    removed += 1

            for id_tag in list(self._idtag_auth_index.keys()):
                self._prune_idtag_index_locked(id_tag, current_time)
            for event_type in list(self._event_index.keys()):
                self._prune_event_index_locked(event_type, current_time)

            logger.info(
                json.dumps(
                    {
                        "event": "KG_PRUNE_OLD_NODES",
                        "removed_nodes": removed,
                        "cutoff_seconds": cutoff_window,
                    }
                )
            )

        return removed


class EVKnowledgeGraph(SecurityKnowledgeGraph):
    """Backward-compatible alias with legacy method names used by existing modules."""

    def __init__(self, time_window_seconds: int = 300) -> None:
        super().__init__(
            auth_window_seconds=time_window_seconds,
            event_fanout_window_seconds=time_window_seconds,
            event_fanout_threshold=3,
        )

    def add_station(self, station_id: str) -> None:
        with self._lock:
            cp = str(station_id)
            cp_node = self._node_id(self.NODE_CHARGE_POINT, cp)
            self._ensure_node(cp_node, self.NODE_CHARGE_POINT, charge_point_id=cp, last_seen=time.time())

    def detect_coordinated_attacks(self) -> List[Dict[str, Any]]:
        alerts = self.detect_coordinated_threats()
        for alert in alerts:
            if "charge_points" in alert and "stations_targeted" not in alert:
                alert["stations_targeted"] = alert["charge_points"]
            if "event_count" not in alert and "charge_points" in alert:
                alert["event_count"] = len(alert["charge_points"])
        return alerts

    def _prune_old_events(self) -> int:
        return self.prune_old_nodes(older_than_seconds=self.event_fanout_window_seconds)


security_knowledge_graph = SecurityKnowledgeGraph()
ev_knowledge_graph = EVKnowledgeGraph()
