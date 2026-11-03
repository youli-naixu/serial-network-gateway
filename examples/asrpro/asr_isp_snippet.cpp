// SPDX-License-Identifier: Apache-2.0
// ASR PRO side of wireless flashing: paste into your TianwenBlock (code mode) project.
//
// When the ESP32 sends "<asr_isp>" on the application UART, reply "<asr_isp:ok>"
// at once, then soft-reset. After the reset the ROM bootloader listens on the
// download port (PB5/PB6) for about 850 ms; the ESP32 catches it there.
//
// 1. Near the top of the file, next to the other extern "C" lines:
extern "C" void dpmu_software_reset_system_config(void);

// 2. In the code that parses frames from the ESP32 (here Serial2, 9600 baud),
//    where a complete payload between '<' and '>' is available:
static bool handle_asr_isp(const char *payload) {
  if (strcmp(payload, "asr_isp") != 0) {
    return false;
  }
  Serial2.print("<asr_isp:ok>");  // reply first: the ESP32 waits up to 300 ms for it
  delay(30);                      // let the UART finish sending
  dpmu_software_reset_system_config();
  return true;                    // not reached
}

// Optional: speak a prompt before restarting. Declare the prompt with the
// other //{playid:...} lines, then use this instead of handle_asr_isp.
// play_audio() returns when playback starts, so wait for it to end; the ESP32
// example keeps trying to catch the bootloader for 8 s.
//
//   //{playid:10500,voice:收到烧录命令，正在重启进入下载模式}
//
extern "C" uint32_t prompt_is_playing();

static bool handle_asr_isp_with_prompt(const char *payload) {
  if (strcmp(payload, "asr_isp") != 0) {
    return false;
  }
  Serial2.print("<asr_isp:ok>");
  play_audio(10500);
  for (int waited = 0; waited < 5000 && prompt_is_playing(); waited += 20) {
    delay(20);
  }
  delay(30);
  dpmu_software_reset_system_config();
  return true;
}
