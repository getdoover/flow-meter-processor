
# Flow Meter Processor

<img src="https://doover.com/wp-content/uploads/Doover-Logo-Landscape-Navy-padded-small.png" alt="App Icon" style="max-width: 300px;">

**Turns raw flow meter signals into flow rate and volume**

[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](https://github.com/getdoover/flow-meter-processor)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/getdoover/flow-meter-processor/blob/main/LICENSE)

[Getting Started](#getting-started) • [Configuration](#configuration) • [Tags](#tags) • [Need Help?](#need-help)

<br/>

## Overview

A cloud processor that reads a raw flow signal from a device's messages and turns it into a
flow rate, today's and yesterday's volume, and a running total. The raw signal can come from
any edge source that publishes to a channel: a Doovit, a LoRaWAN node, a bridge app or
another controller. One install handles one meter, so a site installs it once per meter, for
example on each tank's inflow and each pump's discharge.

Key capabilities:

- **Four input types** -- a cumulative pulse counter, a cumulative totaliser, an instantaneous
  rate, or a 4-20 mA / voltage signal mapped onto a rate range
- **Counter resets handled** -- a counter that drops is treated as a reset or rollover and
  re-baselined, never as negative volume
- **Rate integration** -- Rate and Analog inputs are integrated between readings, over a
  capped gap, to give volume
- **Local days** -- Today and Yesterday roll over at midnight in the configured timezone
- **Display units** -- rate in L/s, L/min, kL/min, m³/h or ML/day; volume in L, kL or ML.
  Tags stay in L/s and litres for other apps to read
- **Sensor fault** -- an analog signal far below its range is a fault, not zero flow
- **Notifications** -- optional no-flow and high-flow notifications

<br/>

## Getting Started

1. Install the app on the device whose messages carry the meter reading.
2. Choose the **Input Type** and set **Input Message Path** to where the reading arrives,
   e.g. `$io_state.flow_volume`.
3. Make sure that channel is in the **Subscription** list (default `io_state`).
4. Set the scaling for the input type, and the display units.

Until the first reading arrives every value shows empty, and nothing is notified.

### Configuration

| Setting | Key | Description | Default |
|---------|-----|-------------|---------|
| **Input Type** | `input_type` | `Pulse Count`, `Totaliser`, `Rate` or `Analog` | `Pulse Count` |
| **Input Message Path** | `input_message_path` | `$channel.path.to.value` holding the raw reading | `$io_state.flow_1` |
| **Litres per Pulse** | `litres_per_pulse` | Pulse Count: litres per pulse | `1.0` |
| **Totaliser Units** | `totaliser_units` | Totaliser: `L`, `kL`, `ML` or `m³` | `L` |
| **Totaliser Multiplier** | `totaliser_multiplier` | Totaliser: extra scale before the units (e.g. `10` for a register in tens) | `1.0` |
| **Input Rate Units** | `rate_units` | Rate / Analog: units of the rate reading or the analog rate range | `L/s` |
| **Analog Input Units** | `analog_input_units` | Analog: `mA` or `V` | `mA` |
| **Analog Minimum / Maximum Input** | `analog_min_input` / `analog_max_input` | Analog: signal at the minimum / maximum rate | `4.0` / `20.0` |
| **Analog Minimum / Maximum Rate** | `analog_min_rate` / `analog_max_rate` | Analog: rate at the minimum / maximum input, in the input rate units | `0.0` / `10.0` |
| **Max Reading Gap (minutes)** | `max_gap_minutes` | Longest gap that still counts as continuous (see below) | `60` |
| **Display Rate Units** | `display_rate_units` | `L/s`, `L/min`, `kL/min`, `m³/h` or `ML/day` | `L/s` |
| **Display Volume Units** | `display_volume_units` | `L`, `kL` or `ML` | `L` |
| **Flowing Threshold** | `flowing_threshold` | A rate above this (display rate units) counts as flowing | `0.0` |
| **Timezone** | `dv_proc_timezone` | Timezone for the Today / Yesterday boundary | `Australia/Brisbane` |
| **No Flow Alert** | `no_flow_alert_enabled` | Notify when nothing has flowed for the No Flow Hours | `false` |
| **No Flow Hours** | `no_flow_alert_hours` | Hours without flow before notifying | `24` |
| **High Flow Alert** | `high_flow_alert_enabled` | Notify when the rate rises above the High Flow Threshold | `false` |
| **High Flow Threshold** | `high_flow_threshold` | Rate (display rate units) for the high flow notification | `10.0` |
| **Subscription** | `dv_proc_subscriptions` | Channels the processor listens to | `["io_state"]` |
| **Schedule** | `dv_proc_schedules` | Periodic check for the day rollover, a stale rate and no flow | `rate(15 minutes)` |

Only the scaling fields for the selected Input Type are shown.

### How each input type is read

| Input Type | Volume | Flow rate |
|------------|--------|-----------|
| **Pulse Count** | increase in count x Litres per Pulse | volume / time between readings |
| **Totaliser** | increase in reading x units x multiplier | volume / time between readings |
| **Rate** | rate integrated between readings (trapezoid) | the reading, converted to L/s |
| **Analog** | rate integrated between readings (trapezoid) | the signal mapped linearly from the input range onto the rate range |

- **Counter resets**: a Pulse Count or Totaliser reading lower than the last is a reset or
  rollover. No volume is counted for that step and the new reading becomes the baseline.
- **The first reading** sets the baseline: Total and Today start at 0 and count from there.
- **Max Reading Gap**: a Rate or Analog reading is integrated over at most this long; a
  counter gives a flow rate only across a gap this short (its volume is still counted over
  any gap); and the flow rate clears once no reading has arrived for this long. Set it above
  the edge's reporting interval.
- **Out-of-order readings** (older than the last one accepted) are ignored.
- **Negative rates** count as zero flow.
- Readings are timed by when their message was created.

#### Sensor fault (Analog)

| Signal | Result |
|--------|--------|
| at or above the minimum input | mapped normally |
| below it, down to 3/16 of the input span (1-4 mA on 4-20 mA) | read as the minimum input |
| further below (a dead loop, under 1 mA) | `sensor_fault` = `under_range` |

While in fault the flow rate is empty, no volume is integrated across the fault, the page
shows a warning with the signal, and no notification fires.

### Days

Today's volume moves to Yesterday at local midnight in the configured timezone. The
scheduled check makes this happen even when no reading arrives around midnight. If a whole
day passes without readings, Yesterday is 0. Volume measured across midnight by a counter
is counted on the day of the reading that measured it.

### Notifications

| Notification | Sent when |
|--------------|-----------|
| **No flow** | No Flow Alert is on, the meter has reported within the Max Reading Gap, has no sensor fault, and has shown no flow for longer than the No Flow Hours. Sent once; re-armed when flow resumes |
| **High flow** | High Flow Alert is on and the rate rises above the High Flow Threshold. Sent once; re-armed when the rate falls back to the threshold |

A meter that has never reported, or has stopped reporting, sends neither.

<br/>

## Integrations

### Channels

| Channel | Direction | Contents |
|---------|-----------|----------|
| The Input Message Path's channel (default `io_state`) | in | The raw reading, at the configured path |
| `tag_values` | out | The tags below, under this install's app key |
| `notifications` | out | The no-flow and high-flow notifications |

This app sends no commands and writes no settings.

### Tags

Canonical units: L/s for rate, litres for volume, milliseconds since epoch for times.

| Tag | Type | Description |
|-----|------|-------------|
| **flow_rate** | number | Flow rate in L/s; `null` when unknown or stale |
| **flow_rate_display** | number | Flow rate in the Display Rate Units |
| **total_volume_l** | number | Running total in litres since the first reading |
| **today_volume_l** | number | Volume so far today (local day), litres |
| **yesterday_volume_l** | number | Volume yesterday (local day), litres |
| **total_volume_display** / **today_volume_display** / **yesterday_volume_display** | number | The volumes in the Display Volume Units |
| **flowing** | boolean | `true` when the rate is above the Flowing Threshold; `null` when unknown |
| **last_flow_ts** | number | Time of the last reading that showed flow |
| **raw_reading** | number | The last raw reading as received |
| **sensor_fault** | string | `under_range` while an analog input is in fault, otherwise `null` |
| **last_reading_ts** / **first_reading_ts** | number | Times of the last and first accepted readings |

The app also keeps its working state in tags (`state_last_value`, `state_input_type`,
`state_day`, `state_fault_input`, `no_flow_alert_active`, `high_flow_alert_active`,
`sensor_fault_hidden`, `sensor_fault_message`) so each invocation carries on from the last.

<br/>

### Need Help?

- Email: support@doover.com
- [Doover Documentation](https://docs.doover.com)

<br/>

## License

This app is licensed under the [Apache License 2.0](https://github.com/getdoover/flow-meter-processor/blob/main/LICENSE).
