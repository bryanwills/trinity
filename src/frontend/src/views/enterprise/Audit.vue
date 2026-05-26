<script setup>
/**
 * Enterprise Audit Log dashboard (#941, v2).
 *
 * Admin-facing read view over the platform audit log. Frontend ships
 * in the OSS bundle; route is gated by `requiresEntitlement: 'audit'`
 * in `router/index.js` so OSS-only deploys (where the entitlement
 * service does not register `'audit'`) bounce to the catalogue.
 *
 * v2 (this revision): stats tiles header, time-preset chips, inline
 * cell drill-down, hash-chain verify badge, CSV/JSON export. All five
 * sit on top of existing audit-log endpoints — no backend changes.
 *
 * Out of scope: SIEM webhook push (separate enterprise pillar),
 * sparkline chart (bundle weight), WebSocket live updates.
 */
import { computed, onMounted, watch } from 'vue'
import { useAuditLogStore } from '../../stores/auditLog'

const store = useAuditLogStore()
// Direct reactive access — Pinia state is already reactive, and the
// template's v-model writes pass through to the store unchanged.
const filters = store.filters

const TIME_PRESETS = [
  { key: '1h', label: 'Last 1h' },
  { key: '24h', label: 'Last 24h' },
  { key: '7d', label: 'Last 7d' },
  { key: '30d', label: 'Last 30d' },
  { key: 'all', label: 'All time' },
]

onMounted(async () => {
  await store.loadDistinct()
  await Promise.all([store.loadList(), store.loadStats()])
})

// Re-load list when offset changes (pagination clicks).
watch(
  () => store.offset,
  () => {
    store.loadList()
  }
)

// Manual time-filter edits flip the preset back to "custom" so the
// chips reflect reality. We watch the two time fields rather than
// hooking the inputs because v-model binds directly to the store.
watch(
  () => [filters.start_time, filters.end_time],
  () => {
    // If the new bounds happen to match an active preset's "now − Xh"
    // computation, we still mark as custom — exact match is too fragile
    // (ms drift). Manual edit always means custom.
    if (
      store.activePreset !== 'custom' &&
      !isPresetSelected(store.activePreset)
    ) {
      store.activePreset = 'custom'
    }
  }
)

function isPresetSelected(_key) {
  // The preset action sets `activePreset` explicitly. Anywhere else
  // that touches `start_time` or `end_time` (manual form edits, the
  // drill-down handler) demotes to 'custom'. This helper exists as a
  // hook for future precise-match logic without changing the watcher.
  return false
}

function applyFilters() {
  store.offset = 0
  store.activePreset = 'custom'
  Promise.all([store.loadList(), store.loadStats()])
}

function resetFilters() {
  store.resetFilters()
  store.loadDistinct(true)
  Promise.all([store.loadList(), store.loadStats()])
}

async function applyPreset(key) {
  await store.applyTimePreset(key)
}

async function drilldownEvent(eventType) {
  if (!eventType) return
  await store.drilldownFilter('event_type', eventType)
}

async function drilldownActor(entry) {
  // Prefer actor_id (queryable). Fall back to actor_type if no id.
  if (entry.actor_id) {
    await store.drilldownFilter('actor_id', entry.actor_id)
  } else if (entry.actor_type) {
    await store.drilldownFilter('actor_type', entry.actor_type)
  }
}

async function verifyChain() {
  await store.verifyChain()
}

async function exportAs(format) {
  await store.downloadExport(format)
}

async function openDetail(entry) {
  // Prefer the in-list payload, but refresh from the detail endpoint to
  // pick up any field the list response truncated. The server returns
  // the same row in both cases today, but keeping the detail call lets
  // us add lazy-loaded fields (e.g. raw `details` JSON) without
  // changing list payload shape later.
  store.selectEntry(entry)
  await store.loadDetail(entry.event_id)
}

function closeDetail() {
  store.clearSelection()
}

function formatTimestamp(ts) {
  if (!ts) return ''
  // Drop the millisecond + trailing Z for tighter table rows.
  return ts.replace('T', ' ').replace(/\.\d+/, '').replace(/Z$/, ' UTC')
}

function actorLabel(entry) {
  return entry.actor_email || entry.actor_id || `(${entry.actor_type})`
}

function targetLabel(entry) {
  if (!entry.target_type && !entry.target_id) return '—'
  if (!entry.target_id) return entry.target_type
  if (!entry.target_type) return entry.target_id
  return `${entry.target_type}/${entry.target_id}`
}

const detailsJson = computed(() => {
  const e = store.selectedEntry
  if (!e || !e.details) return ''
  if (typeof e.details === 'string') return e.details
  try {
    return JSON.stringify(e.details, null, 2)
  } catch {
    return String(e.details)
  }
})
</script>

<template>
  <div class="audit-dashboard p-6 max-w-7xl mx-auto">
    <header class="mb-6">
      <div class="flex items-center gap-3 mb-2">
        <router-link
          to="/enterprise"
          class="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
        >
          ← Enterprise
        </router-link>
      </div>
      <div class="flex items-center gap-3 mb-2">
        <h1 class="text-3xl font-semibold text-gray-900 dark:text-white">Audit Log</h1>
        <span class="px-2 py-0.5 text-xs font-bold rounded bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-200">
          PRO
        </span>
      </div>
      <p class="text-sm text-gray-500 dark:text-gray-400">
        Tamper-evident record of administrative actions. Default filter
        shows the last 24 hours.
      </p>

      <!-- Hash-chain verify badge — manual trigger, visible-range only. -->
      <div class="mt-3 flex items-center gap-2">
        <span
          class="inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-medium rounded"
          :class="{
            'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300':
              store.verifyState === 'idle',
            'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-200':
              store.verifyState === 'verifying',
            'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-200':
              store.verifyState === 'valid',
            'bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-200':
              store.verifyState === 'invalid',
            'bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-200':
              store.verifyState === 'error',
          }"
        >
          <span v-if="store.verifyState === 'idle'">Hash chain · not verified</span>
          <span v-else-if="store.verifyState === 'verifying'">Verifying…</span>
          <span v-else-if="store.verifyState === 'valid'">
            ✓ Valid · {{ store.verifyResult?.checked || 0 }} entries
          </span>
          <span v-else-if="store.verifyState === 'invalid'">
            ✗ Tamper detected · first invalid id #{{ store.verifyResult?.first_invalid_id }}
          </span>
          <span v-else>⚠ Verify failed</span>
        </span>
        <button
          class="px-2 py-0.5 text-xs rounded border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
          :disabled="store.verifyState === 'verifying' || store.entries.length === 0"
          :title="
            store.entries.length === 0
              ? 'Load some entries first.'
              : `Verify ids #${Math.min(...store.entries.map(e => e.id))}–#${Math.max(...store.entries.map(e => e.id))} on this page`
          "
          @click="verifyChain"
        >
          Verify visible range
        </button>
      </div>
    </header>

    <!-- Stats tiles -->
    <section class="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
      <div class="p-3 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        <div class="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wider">
          Total events
        </div>
        <div class="text-2xl font-semibold text-gray-900 dark:text-white mt-1">
          {{ store.statsLoading ? '…' : (store.stats?.total ?? '—') }}
        </div>
        <div class="text-[11px] text-gray-400 mt-1">in window</div>
      </div>

      <button
        class="p-3 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-left hover:border-blue-400 transition disabled:opacity-60 disabled:hover:border-gray-200 disabled:cursor-default"
        :disabled="!store.topEventType"
        :title="store.topEventType ? `Click to filter by ${store.topEventType.key}` : ''"
        @click="store.topEventType && drilldownEvent(store.topEventType.key)"
      >
        <div class="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wider">
          Top event type
        </div>
        <div class="text-lg font-semibold text-gray-900 dark:text-white mt-1 truncate">
          {{ store.topEventType?.key || '—' }}
        </div>
        <div class="text-[11px] text-gray-400 mt-1">
          {{ store.topEventType ? `${store.topEventType.count} events` : 'no data' }}
        </div>
      </button>

      <button
        class="p-3 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-left hover:border-blue-400 transition disabled:opacity-60 disabled:hover:border-gray-200 disabled:cursor-default"
        :disabled="!store.topActorType"
        :title="store.topActorType ? `Click to filter by actor_type=${store.topActorType.key}` : ''"
        @click="store.topActorType && store.drilldownFilter('actor_type', store.topActorType.key)"
      >
        <div class="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wider">
          Top actor type
        </div>
        <div class="text-lg font-semibold text-gray-900 dark:text-white mt-1 truncate">
          {{ store.topActorType?.key || '—' }}
        </div>
        <div class="text-[11px] text-gray-400 mt-1">
          {{ store.topActorType ? `${store.topActorType.count} events` : 'no data' }}
        </div>
      </button>

      <div class="p-3 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        <div class="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wider">
          Time window
        </div>
        <div class="text-sm font-medium text-gray-900 dark:text-white mt-1 break-all">
          {{ store.timeWindowLabel }}
        </div>
        <div class="text-[11px] text-gray-400 mt-1">{{ store.activePreset }}</div>
      </div>
    </section>

    <!-- Time-preset chips -->
    <div class="flex flex-wrap items-center gap-2 mb-4">
      <span class="text-xs text-gray-500 dark:text-gray-400 mr-1">Time:</span>
      <button
        v-for="p in TIME_PRESETS"
        :key="p.key"
        class="px-2.5 py-1 text-xs font-medium rounded-full transition"
        :class="
          store.activePreset === p.key
            ? 'bg-blue-600 text-white'
            : 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-200 hover:bg-gray-200 dark:hover:bg-gray-600'
        "
        @click="applyPreset(p.key)"
      >
        {{ p.label }}
      </button>
      <span
        v-if="store.activePreset === 'custom'"
        class="px-2.5 py-1 text-xs font-medium rounded-full bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-200"
      >
        Custom
      </span>
    </div>

    <!-- Filter form -->
    <section
      class="mb-4 p-4 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800"
    >
      <h2 class="text-sm font-medium text-gray-700 dark:text-gray-200 mb-3">
        Filters
      </h2>
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1"
            >Event type</label
          >
          <select
            v-model="filters.event_type"
            class="w-full px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
          >
            <option value="">All</option>
            <option v-for="t in store.distinctEventTypes" :key="t" :value="t">
              {{ t }}
            </option>
          </select>
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1"
            >Actor type</label
          >
          <select
            v-model="filters.actor_type"
            class="w-full px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
          >
            <option value="">All</option>
            <option v-for="t in store.distinctActorTypes" :key="t" :value="t">
              {{ t }}
            </option>
          </select>
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1"
            >Actor ID</label
          >
          <input
            v-model="filters.actor_id"
            type="text"
            placeholder="user.id or agent_name"
            class="w-full px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
          />
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1"
            >Target type</label
          >
          <input
            v-model="filters.target_type"
            type="text"
            placeholder="agent / user / schedule…"
            class="w-full px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
          />
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1"
            >Start (ISO 8601 UTC)</label
          >
          <input
            v-model="filters.start_time"
            type="text"
            placeholder="2026-05-25T00:00:00Z"
            class="w-full px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
          />
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1"
            >End (ISO 8601 UTC)</label
          >
          <input
            v-model="filters.end_time"
            type="text"
            placeholder="leave blank for now"
            class="w-full px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
          />
        </div>
      </div>
      <div class="mt-3 flex flex-wrap items-center gap-2">
        <button
          class="px-3 py-1.5 text-sm font-medium rounded bg-blue-600 text-white hover:bg-blue-700"
          @click="applyFilters"
        >
          Apply
        </button>
        <button
          class="px-3 py-1.5 text-sm font-medium rounded border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700"
          @click="resetFilters"
        >
          Reset
        </button>
        <div class="flex-1"></div>
        <span class="text-xs text-gray-500 dark:text-gray-400 hidden sm:inline">
          Export current view:
        </span>
        <button
          class="px-3 py-1.5 text-sm font-medium rounded border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
          :disabled="store.exporting"
          title="Download a CSV of the current filter window (uses /api/audit-log/export)."
          @click="exportAs('csv')"
        >
          ⬇ CSV
        </button>
        <button
          class="px-3 py-1.5 text-sm font-medium rounded border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
          :disabled="store.exporting"
          title="Download a JSON array of the current filter window."
          @click="exportAs('json')"
        >
          ⬇ JSON
        </button>
      </div>
      <div
        v-if="store.error"
        class="mt-2 text-xs text-red-600 dark:text-red-400"
      >
        {{ store.error }}
      </div>
    </section>

    <!-- Table + detail layout -->
    <section class="flex gap-4">
      <div
        class="flex-1 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 overflow-hidden"
        :class="store.selectedEntry ? 'lg:max-w-3xl' : ''"
      >
        <div v-if="store.loading" class="p-6 text-sm text-gray-500 text-center">
          Loading…
        </div>
        <div
          v-else-if="!store.entries.length"
          class="p-6 text-sm text-gray-500 text-center"
        >
          {{
            store.total === 0
              ? 'No audit entries match these filters.'
              : 'No results on this page.'
          }}
        </div>
        <table v-else class="w-full text-sm">
          <thead class="bg-gray-50 dark:bg-gray-900 text-left">
            <tr class="text-xs uppercase tracking-wider text-gray-500 dark:text-gray-400">
              <th class="px-3 py-2 font-medium">Timestamp</th>
              <th class="px-3 py-2 font-medium">Event</th>
              <th class="px-3 py-2 font-medium">Actor</th>
              <th class="px-3 py-2 font-medium">Target</th>
              <th class="px-3 py-2 font-medium">Source</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="entry in store.entries"
              :key="entry.event_id"
              class="border-t border-gray-100 dark:border-gray-700 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700"
              :class="
                store.selectedEntry?.event_id === entry.event_id
                  ? 'bg-blue-50 dark:bg-blue-900/20'
                  : ''
              "
              @click="openDetail(entry)"
            >
              <td class="px-3 py-2 font-mono text-xs text-gray-600 dark:text-gray-300 whitespace-nowrap">
                {{ formatTimestamp(entry.timestamp) }}
              </td>
              <td class="px-3 py-2 text-gray-900 dark:text-white">
                <button
                  class="font-medium underline-offset-2 hover:underline hover:text-blue-700 dark:hover:text-blue-300"
                  :title="`Filter by event_type=${entry.event_type}`"
                  @click.stop="drilldownEvent(entry.event_type)"
                >
                  {{ entry.event_type }}
                </button>
                <span class="ml-1 text-xs text-gray-500 dark:text-gray-400"
                  >· {{ entry.event_action }}</span
                >
              </td>
              <td class="px-3 py-2 text-gray-700 dark:text-gray-200">
                <button
                  class="underline-offset-2 hover:underline hover:text-blue-700 dark:hover:text-blue-300"
                  :title="
                    entry.actor_id
                      ? `Filter by actor_id=${entry.actor_id}`
                      : `Filter by actor_type=${entry.actor_type}`
                  "
                  @click.stop="drilldownActor(entry)"
                >
                  {{ actorLabel(entry) }}
                </button>
              </td>
              <td class="px-3 py-2 text-gray-700 dark:text-gray-200">
                {{ targetLabel(entry) }}
              </td>
              <td class="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">
                {{ entry.source }}
              </td>
            </tr>
          </tbody>
        </table>
        <footer
          class="flex items-center justify-between px-4 py-2 border-t border-gray-100 dark:border-gray-700 text-xs text-gray-500 dark:text-gray-400"
        >
          <span>{{ store.rangeLabel }}</span>
          <span class="flex items-center gap-2">
            <button
              class="px-2 py-0.5 rounded border border-gray-300 dark:border-gray-600 disabled:opacity-40"
              :disabled="!store.hasPrev || store.loading"
              @click="store.prevPage()"
            >
              ← Prev
            </button>
            <span>Page {{ store.page }} of {{ store.pageCount }}</span>
            <button
              class="px-2 py-0.5 rounded border border-gray-300 dark:border-gray-600 disabled:opacity-40"
              :disabled="!store.hasNext || store.loading"
              @click="store.nextPage()"
            >
              Next →
            </button>
          </span>
        </footer>
      </div>

      <!-- Side detail panel -->
      <aside
        v-if="store.selectedEntry"
        class="hidden lg:block flex-1 max-w-lg rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-4 overflow-y-auto self-start"
      >
        <header class="flex items-start justify-between mb-3">
          <div>
            <h3 class="text-base font-medium text-gray-900 dark:text-white">
              {{ store.selectedEntry.event_type }} ·
              {{ store.selectedEntry.event_action }}
            </h3>
            <p class="text-xs text-gray-500 dark:text-gray-400 font-mono mt-0.5">
              {{ store.selectedEntry.event_id }}
            </p>
          </div>
          <button
            class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
            aria-label="Close detail"
            @click="closeDetail"
          >
            ✕
          </button>
        </header>

        <dl class="grid grid-cols-3 gap-x-3 gap-y-1 text-xs mb-3">
          <dt class="text-gray-500 dark:text-gray-400">Timestamp</dt>
          <dd class="col-span-2 font-mono text-gray-900 dark:text-white">
            {{ store.selectedEntry.timestamp }}
          </dd>

          <dt class="text-gray-500 dark:text-gray-400">Actor</dt>
          <dd class="col-span-2 text-gray-900 dark:text-white">
            {{ store.selectedEntry.actor_type }} ·
            {{ actorLabel(store.selectedEntry) }}
          </dd>

          <template v-if="store.selectedEntry.actor_ip">
            <dt class="text-gray-500 dark:text-gray-400">Actor IP</dt>
            <dd class="col-span-2 font-mono text-gray-900 dark:text-white">
              {{ store.selectedEntry.actor_ip }}
            </dd>
          </template>

          <template v-if="store.selectedEntry.mcp_key_name">
            <dt class="text-gray-500 dark:text-gray-400">MCP key</dt>
            <dd class="col-span-2 text-gray-900 dark:text-white">
              {{ store.selectedEntry.mcp_key_name }} ({{ store.selectedEntry.mcp_scope || 'unknown scope' }})
            </dd>
          </template>

          <dt class="text-gray-500 dark:text-gray-400">Target</dt>
          <dd class="col-span-2 text-gray-900 dark:text-white">
            {{ targetLabel(store.selectedEntry) }}
          </dd>

          <dt class="text-gray-500 dark:text-gray-400">Source</dt>
          <dd class="col-span-2 text-gray-900 dark:text-white">
            {{ store.selectedEntry.source }}
            <span
              v-if="store.selectedEntry.endpoint"
              class="text-gray-500 dark:text-gray-400 font-mono"
            >
              ({{ store.selectedEntry.endpoint }})
            </span>
          </dd>

          <template v-if="store.selectedEntry.request_id">
            <dt class="text-gray-500 dark:text-gray-400">Request</dt>
            <dd class="col-span-2 font-mono text-gray-900 dark:text-white">
              {{ store.selectedEntry.request_id }}
            </dd>
          </template>
        </dl>

        <details class="mb-3" open>
          <summary class="text-xs font-medium text-gray-600 dark:text-gray-300 cursor-pointer">
            Details JSON
          </summary>
          <pre
            class="mt-2 p-2 rounded bg-gray-50 dark:bg-gray-900 text-xs text-gray-800 dark:text-gray-200 overflow-x-auto"
          >{{ detailsJson || '(none)' }}</pre>
        </details>

        <details>
          <summary class="text-xs font-medium text-gray-600 dark:text-gray-300 cursor-pointer">
            Hash chain
          </summary>
          <dl class="mt-2 text-xs grid grid-cols-3 gap-x-3 gap-y-1">
            <dt class="text-gray-500 dark:text-gray-400">previous_hash</dt>
            <dd class="col-span-2 font-mono break-all text-gray-900 dark:text-white">
              {{ store.selectedEntry.previous_hash || '(none)' }}
            </dd>
            <dt class="text-gray-500 dark:text-gray-400">entry_hash</dt>
            <dd class="col-span-2 font-mono break-all text-gray-900 dark:text-white">
              {{ store.selectedEntry.entry_hash || '(none)' }}
            </dd>
          </dl>
        </details>
      </aside>
    </section>
  </div>
</template>
