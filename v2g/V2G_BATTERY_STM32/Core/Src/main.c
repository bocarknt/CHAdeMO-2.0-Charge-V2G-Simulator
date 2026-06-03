/* USER CODE BEGIN Header */
/**
 ******************************************************************************
 * @file    main.c
 * @brief   Battery MC — CHAdeMO 2.0 V2G
 *          Pont UART (battery sim) ↔ CAN (station MC)
 *
 * Phase 0 : rx UART MSG100 → TX CAN 0x100
 *           rx CAN  0x108  → TX UART MSG108
 *           rx UART MSG101 → TX CAN 0x101
 *           rx CAN  0x109  → TX UART MSG109
 *
 * Phase 1 : rx UART MSG200 → TX CAN 0x200
 *           rx CAN  0x208  → TX UART MSG208
 *
 * Phase 2 : rx UART MSG201 → TX CAN 0x201
 *           rx CAN  0x209  → TX UART MSG209
 *
 * Phase 3 (boucle 100ms) :
 *           rx UART MSG102 → TX CAN 0x102
 *           rx UART MSG200 → TX CAN 0x200
 *           rx CAN  0x109  → TX UART MSG109  (non-bloquant)
 *           rx CAN  0x208  → TX UART MSG208  (non-bloquant)
 *
 * Phase 4 : arrêt propre → TX UART STATE:IDLE → reboucle while(1)
 ******************************************************************************
 */
/* USER CODE END Header */

#include "main.h"
#include "can.h"
#include "usart.h"
#include "gpio.h"
#include <string.h>
#include <stdio.h>
#include <stdlib.h>

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */

/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */
uint16_t battery_voltage_min        = 250;
uint16_t battery_voltage_max        = 400;
uint16_t min_charge_current         = 0;
uint8_t  soc                        = 80;
uint8_t  total_capacity_kwh         = 0;
uint16_t target_charge_voltage      = 400;
uint8_t  max_charge_current         = 0;
uint8_t  max_discharge_current      = 0;
uint16_t min_discharge_voltage      = 300;
uint8_t  min_soc_for_discharging    = 20;
uint8_t  max_soc_for_charging       = 90;
uint8_t  sequence_control_number    = 0x01;
uint8_t  vehicle_charging_enabled   = 1;
uint8_t  normal_stop_request        = 0;
uint8_t  vehicle_status             = 1;
uint8_t  vehicle_shift_position     = 0;
uint8_t  charging_system_error      = 0;
uint8_t  battery_overvoltage        = 0;
uint8_t  battery_undervoltage       = 0;
uint8_t  battery_current_dev_error  = 0;
uint8_t  high_battery_temperature   = 0;
uint8_t  battery_voltage_dev_error  = 0;

uint16_t evse_available_voltage    = 0;
uint8_t  evse_available_current    = 0;
uint16_t evse_threshold_voltage    = 0;
uint8_t  evse_welding_detection    = 0;
uint8_t  evse_discharge_compatible = 0;
uint8_t  evse_stop_control         = 1;
uint8_t  evse_system_error         = 0;
uint16_t evse_present_voltage      = 0;
uint8_t  evse_present_current      = 0;
uint8_t  evse_present_discharge_i  = 0;
uint16_t evse_input_voltage        = 0;
uint8_t  evse_input_current        = 0;
uint16_t evse_lower_threshold_v    = 0;
uint8_t  evse_sequence_number      = 0;

volatile uint8_t flag_msg_108 = 0;
volatile uint8_t flag_msg_109 = 0;
volatile uint8_t flag_msg_208 = 0;
volatile uint8_t flag_msg_209 = 0;
uint8_t rxData_108[8];
uint8_t rxData_109[8];
uint8_t rxData_208[8];
uint8_t rxData_209[8];

uint8_t txbuf[256];
int     Cable_Detected = 0;

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
#define RX_BUF_SIZE 128

static uint8_t          rx_byte;
static char             rx_buf[RX_BUF_SIZE];
static uint16_t         rx_idx        = 0;
static char             rx_line[RX_BUF_SIZE];
static volatile uint8_t rx_line_ready = 0;

void UART_RX_Start(void)
{
    rx_idx = 0; rx_line_ready = 0;
    HAL_UART_Receive_IT(&huart2, &rx_byte, 1);
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance != USART2) return;
    char c = (char)rx_byte;
    if (c == '\n') {
        if (rx_idx > 0 && rx_buf[rx_idx - 1] == '\r') rx_idx--;
        rx_buf[rx_idx] = '\0';
        strncpy(rx_line, rx_buf, RX_BUF_SIZE - 1);
        rx_line[RX_BUF_SIZE - 1] = '\0';
        rx_idx = 0; rx_line_ready = 1;
    } else {
        if (rx_idx < RX_BUF_SIZE - 1) rx_buf[rx_idx++] = c;
    }
    HAL_UART_Receive_IT(&huart2, &rx_byte, 1);
}

void UART_Send(const char *msg)
{
    HAL_UART_Transmit(&huart2, (uint8_t *)msg, strlen(msg), 100);
}

static int get_field(const char *line, int index)
{
    const char *p = strchr(line, ':');
    if (!p) return 0; p++;
    for (int i = 0; i < index; i++) {
        p = strchr(p, ',');
        if (!p) return 0; p++;
    }
    return atoi(p);
}

/**
 * @brief Attend une ligne UART. Ignore les lignes DBG: du firmware.
 */
static void wait_for_line(char *out)
{
    do {
        while (!rx_line_ready) { HAL_Delay(1); }
        __disable_irq();
        strncpy(out, rx_line, RX_BUF_SIZE - 1);
        out[RX_BUF_SIZE - 1] = '\0';
        rx_line_ready = 0;
        __enable_irq();
    } while (strncmp(out, "DBG:", 4) == 0);  /* ignore les messages debug */
}

static void CAN_Send(uint32_t id, uint8_t *data, uint8_t len)
{
    CAN_TxHeaderTypeDef h; uint32_t mb;
    h.StdId = id; h.IDE = CAN_ID_STD; h.RTR = CAN_RTR_DATA; h.DLC = len;
    if (HAL_CAN_AddTxMessage(&hcan1, &h, data, &mb) != HAL_OK)
        UART_Send("STATE:CAN_ERROR\r\n");
}

/* ── TX CAN ────────────────────────────────────────────────────────────────── */

static void send_0x100(void)
{
    uint8_t d[8] = {0};
    d[0] = min_charge_current;
    d[2] = battery_voltage_min & 0xFF; d[3] = battery_voltage_min >> 8;
    d[4] = battery_voltage_max & 0xFF; d[5] = battery_voltage_max >> 8;
    d[6] = 0x64;
    CAN_Send(0x100, d, 8);
}

static void send_0x101(void)
{
    uint8_t d[8] = {0};
    d[1] = 0xFF; d[2] = 30; d[3] = 20;
    d[5] = total_capacity_kwh & 0xFF; d[6] = total_capacity_kwh >> 8;
    CAN_Send(0x101, d, 8);
}

static void send_0x102(void)
{
    uint8_t d[8] = {0};
    d[0] = 0x03;
    d[1] = target_charge_voltage & 0xFF; d[2] = target_charge_voltage >> 8;
    d[3] = max_charge_current;
    d[4] = (battery_voltage_dev_error << 4) | (high_battery_temperature   << 3) |
           (battery_current_dev_error << 2) | (battery_undervoltage       << 1) |
           (battery_overvoltage       << 0);
    d[5] = (normal_stop_request      << 4) | (vehicle_status             << 3) |
           (charging_system_error    << 2) | (vehicle_shift_position     << 1) |
           (vehicle_charging_enabled << 0);
    d[6] = soc;
    CAN_Send(0x102, d, 8);
}

static void send_0x200(void)
{
    uint8_t d[8] = {0};
    d[0] = 0xFF - max_discharge_current;
    d[4] = min_discharge_voltage & 0xFF; d[5] = min_discharge_voltage >> 8;
    d[6] = min_soc_for_discharging;
    d[7] = max_soc_for_charging;
    CAN_Send(0x200, d, 8);
}

static void send_0x201(uint16_t energy, uint16_t dis_t)
{
    uint8_t d[8] = {0};
    d[0] = sequence_control_number;
    d[1] = dis_t & 0xFF; d[2] = dis_t >> 8;
    d[3] = energy & 0xFF; d[4] = energy >> 8;
    CAN_Send(0x201, d, 8);
}

/* ── RX CAN → forward UART ─────────────────────────────────────────────────── */

static void process_0x108(void)
{
    if (!flag_msg_108) return; flag_msg_108 = 0;
    evse_welding_detection = rxData_108[0] & 0x01;
    evse_available_voltage = rxData_108[1] | (rxData_108[2] << 8);
    evse_available_current = rxData_108[3];
    evse_threshold_voltage = rxData_108[4] | (rxData_108[5] << 8);
    sprintf((char *)txbuf, "MSG108:%d,%d,%d,%d\r\n",
            evse_welding_detection, evse_available_voltage,
            evse_available_current, evse_threshold_voltage);
    UART_Send((char *)txbuf);
}

static void process_0x109(void)
{
    if (!flag_msg_109) return; flag_msg_109 = 0;
    evse_present_voltage      = rxData_109[1] | (rxData_109[2] << 8);
    evse_present_current      = rxData_109[3];
    evse_discharge_compatible = rxData_109[4] & 0x01;
    evse_stop_control         = (rxData_109[5] >> 5) & 0x01;
    evse_system_error         = (rxData_109[5] >> 4) & 0x01;
    sprintf((char *)txbuf, "MSG109:%d,%d,%d,%d,%d,%d,%d\r\n",
            rxData_109[0], evse_present_voltage, evse_present_current,
            evse_discharge_compatible, rxData_109[5],
            rxData_109[6], rxData_109[7]);
    UART_Send((char *)txbuf);
}

static void process_0x208(void)
{
    if (!flag_msg_208) return; flag_msg_208 = 0;
    evse_present_discharge_i = rxData_208[0];
    evse_input_voltage       = rxData_208[1] | (rxData_208[2] << 8);
    evse_input_current       = rxData_208[3];
    evse_lower_threshold_v   = rxData_208[6] | (rxData_208[7] << 8);
    sprintf((char *)txbuf, "MSG208:%d,%d,%d,%d\r\n",
            evse_present_discharge_i, evse_input_voltage,
            evse_input_current, evse_lower_threshold_v);
    UART_Send((char *)txbuf);
}

static void process_0x209(void)
{
    if (!flag_msg_209) return; flag_msg_209 = 0;
    evse_sequence_number = rxData_209[0];
    uint16_t rem = rxData_209[1] | (rxData_209[2] << 8);
    sprintf((char *)txbuf, "MSG209:%d,%d\r\n", evse_sequence_number, rem);
    UART_Send((char *)txbuf);
}

/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
/* ── main ──────────────────────────────────────────────────────────────────── */

int main(void)
{
    HAL_Init();
    SystemClock_Config();
    MX_GPIO_Init();
    MX_USART2_UART_Init();
    MX_CAN1_Init();

    CAN_Filter_Config();
    HAL_CAN_Start(&hcan1);
    HAL_CAN_ActivateNotification(&hcan1, CAN_IT_RX_FIFO0_MSG_PENDING);
    UART_RX_Start();

    char line[RX_BUF_SIZE];

    while (1)
    {
        normal_stop_request      = 0;
        vehicle_charging_enabled = 1;
        evse_stop_control        = 1;

        /* ── Phase 0 — Handshake ─────────────────────────────────────────── */
        UART_Send("STATE:HANDSHAKE\r\n");

        /* 1. Attente MSG100 UART → TX CAN 0x100 */
        wait_for_line(line);
        if (strncmp(line, "MSG100:", 7) == 0) {
            min_charge_current  = get_field(line, 0);
            battery_voltage_min = get_field(line, 1);
            battery_voltage_max = get_field(line, 2);
            send_0x100();
        }

        /* 2. Attente CAN 0x108 → TX UART MSG108
              battery sim attend MSG108 avant d'envoyer MSG101 */
        while (!flag_msg_108) { HAL_Delay(1); }
        process_0x108();

        /* 3. Attente MSG101 UART → TX CAN 0x101
              battery sim envoie MSG101 automatiquement après MSG108 */
        wait_for_line(line);
        if (strncmp(line, "MSG101:", 7) == 0) {
            total_capacity_kwh = get_field(line, 3);
            send_0x101();
        }

        /* 4. Attente CAN 0x109 → TX UART MSG109
              station sim envoie MSG109 automatiquement après MSG101 */
        while (!flag_msg_109) { HAL_Delay(1); }
        process_0x109();

        if (!evse_discharge_compatible) {
            UART_Send("STATE:CHARGE_ONLY\r\n");
            goto charge_loop;
        }

        /* ── Phase 1 — Négociation V2G ───────────────────────────────────── */
        UART_Send("STATE:V2G_NEGOTIATE\r\n");

        /* 5. Attente MSG200 UART → TX CAN 0x200 */
        wait_for_line(line);
        if (strncmp(line, "MSG200:", 7) == 0) {
            max_discharge_current   = get_field(line, 0);
            min_discharge_voltage   = get_field(line, 1);
            min_soc_for_discharging = get_field(line, 2);
            max_soc_for_charging    = get_field(line, 3);
            send_0x200();
        }

        /* 6. Attente CAN 0x208 → TX UART MSG208 */
        while (!flag_msg_208) { HAL_Delay(1); }
        process_0x208();

        /* ── Phase 2 — Accord séquence ───────────────────────────────────── */
        UART_Send("STATE:V2G_SEQUENCE\r\n");

        /* 7. Attente MSG201 UART → TX CAN 0x201 */
        wait_for_line(line);
        if (strncmp(line, "MSG201:", 7) == 0) {
            sequence_control_number = get_field(line, 0);
            uint16_t dis_t          = get_field(line, 1);
            uint16_t energy         = get_field(line, 2);
            send_0x201(energy, dis_t);
        }

        /* 8. Attente CAN 0x209 → TX UART MSG209 */
        while (!flag_msg_209) { HAL_Delay(1); }
        process_0x209();

        if (evse_sequence_number != sequence_control_number) {
            UART_Send("STATE:SEQ_MISMATCH\r\n");
            continue;
        }

        /* ── Phase 3 — Boucle décharge V2G (100ms) ──────────────────────── */
        evse_stop_control = 0;
        evse_system_error = 0;
        flag_msg_109      = 0;
        flag_msg_208      = 0;
        UART_Send("STATE:DISCHARGING\r\n");

        do {
            /* 9a. Attente MSG102 UART → TX CAN 0x102 */
            wait_for_line(line);
            if (strncmp(line, "MSG102:", 7) == 0) {
                target_charge_voltage    = get_field(line, 0);
                max_charge_current       = get_field(line, 1);
                soc                      = get_field(line, 2);
                vehicle_charging_enabled = get_field(line, 3);
                normal_stop_request      = get_field(line, 4);
                send_0x102();
            }

            /* 9b. Attente MSG200 UART → TX CAN 0x200
                  (battery sim envoie MSG200 juste après MSG102) */
            wait_for_line(line);
            if (strncmp(line, "MSG200:", 7) == 0) {
                max_discharge_current = get_field(line, 0);
                send_0x200();
            }

            /* 9c. Traitement CAN 0x109 → TX UART MSG109 (non-bloquant)
                  Le station sim répond dès que Phase 3 est active.
                  On ne bloque pas ici pour ne pas bloquer la boucle. */
            if (flag_msg_109) {
                process_0x109();
            }

            /* 9d. Traitement CAN 0x208 → TX UART MSG208 (non-bloquant) */
            if (flag_msg_208) {
                process_0x208();
            }

            /* Conditions d'arrêt */
            if (soc <= min_soc_for_discharging) {
                UART_Send("STATE:SOC_MIN\r\n");
                normal_stop_request      = 1;
                vehicle_charging_enabled = 0;
            }
            if (evse_stop_control || evse_system_error) {
                UART_Send("STATE:EVSE_STOP\r\n");
                break;
            }

        } while (!normal_stop_request && vehicle_charging_enabled);

        /* ── Phase 4 — Arrêt propre ──────────────────────────────────────── */
        UART_Send("STATE:STOPPING\r\n");
        normal_stop_request      = 1;
        vehicle_charging_enabled = 0;
        vehicle_status           = 1;
        send_0x102();          /* stop_request=1, charging_enabled=0 */
        max_discharge_current = 0;
        send_0x200();          /* courant décharge = 0 */
        HAL_Delay(500);
        UART_Send("STATE:IDLE\r\n");
        continue;

charge_loop:
        /* Mode charge simple (EVSE non V2G compatible) */
        do {
            wait_for_line(line);
            if (strncmp(line, "MSG102:", 7) == 0) {
                target_charge_voltage    = get_field(line, 0);
                max_charge_current       = get_field(line, 1);
                soc                      = get_field(line, 2);
                vehicle_charging_enabled = get_field(line, 3);
                normal_stop_request      = get_field(line, 4);
                send_0x102();
                while (!flag_msg_109) { HAL_Delay(1); }
                process_0x109();
            }
        } while (!normal_stop_request);
    }
}


/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  if (HAL_PWREx_ControlVoltageScaling(PWR_REGULATOR_VOLTAGE_SCALE1) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSI;
  RCC_OscInitStruct.PLL.PLLM = 1;
  RCC_OscInitStruct.PLL.PLLN = 10;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV7;
  RCC_OscInitStruct.PLL.PLLQ = RCC_PLLQ_DIV2;
  RCC_OscInitStruct.PLL.PLLR = RCC_PLLR_DIV2;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV1;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_4) != HAL_OK)
  {
    Error_Handler();
  }
}

/* USER CODE BEGIN 4 */
void CAN_Filter_Config(void)
{
    CAN_FilterTypeDef f;
    f.FilterActivation     = ENABLE;
    f.FilterBank           = 0;
    f.FilterFIFOAssignment = CAN_FILTER_FIFO0;
    f.FilterMode           = CAN_FILTERMODE_IDMASK;
    f.FilterScale          = CAN_FILTERSCALE_32BIT;
    f.FilterIdHigh = f.FilterIdLow = 0x0000;
    f.FilterMaskIdHigh = f.FilterMaskIdLow = 0x0000;
    HAL_CAN_ConfigFilter(&hcan1, &f);
}

void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin)
{
    Cable_Detected      = 1;
    normal_stop_request = 1;
}

void HAL_CAN_RxFifo0MsgPendingCallback(CAN_HandleTypeDef *hcan)
{
    CAN_RxHeaderTypeDef rxh;
    uint8_t tmp[8];
    if (HAL_CAN_GetRxMessage(hcan, CAN_RX_FIFO0, &rxh, tmp) != HAL_OK) return;
    switch (rxh.StdId) {
        case 0x108: memcpy(rxData_108, tmp, 8); flag_msg_108 = 1; break;
        case 0x109: memcpy(rxData_109, tmp, 8); flag_msg_109 = 1; break;
        case 0x208: memcpy(rxData_208, tmp, 8); flag_msg_208 = 1; break;
        case 0x209: memcpy(rxData_209, tmp, 8); flag_msg_209 = 1; break;
        case 0x7FF:
            UART_Send("RESET\r\n");
            HAL_Delay(10);
            HAL_NVIC_SystemReset();
            break;
    }
}

/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
