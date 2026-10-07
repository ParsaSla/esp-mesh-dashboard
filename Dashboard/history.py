from collections import defaultdict, deque


class HistoryBuffer:
    def __init__(self, limit=1000):
        self.limit = max(1, int(limit))
        self._history = defaultdict(deque)

    def add(self, node_id, sample):
        queue = self._history[node_id]
        queue.append(sample)
        while len(queue) > self.limit:
            queue.popleft()

    def get(self, node_id, since=None, limit=100):
        queue = self._history.get(node_id, deque())
        samples = list(queue)

        if since:
            samples = [sample for sample in samples if sample["timestamp"] >= since]

        if limit is not None:
            samples = samples[-max(1, int(limit)) :]

        return samples

    def latest(self, node_id):
        queue = self._history.get(node_id, deque())
        if not queue:
            return None
        return list(queue)[-1]

    def clear(self):
        self._history.clear()
