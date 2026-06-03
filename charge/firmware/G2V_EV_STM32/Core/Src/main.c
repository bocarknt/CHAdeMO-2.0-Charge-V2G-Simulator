/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "can.h"
#include "usart.h"
#include "gpio.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <stdio.h>
#include<stdlib.h>
#include <string.h>
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
//Parametre de la borne
uint16_t Available_output_voltage_min;      // V
uint16_t Available_output_voltage_max;      // V
uint16_t Available_output_voltage;      // V
uint16_t Threshold_voltage;  //V
uint16_t Available_output_current_min;      // A
uint16_t Available_output_current_max;
uint16_t Available_output_current=0;  //
uint16_t Present_output_current;
uint16_t Present_output_voltage;


uint8_t Battery_incompatibility =0;
uint8_t Welding_detection =1;
volatile uint8_t can_rx_flag = 0;
//uint8_t rxData[32];
uint8_t rxbuf[255];
uint8_t txbuf[255];
// differentes états
typedef enum
{
    EV_STATE_IDLE = 0,
    EV_STATE_CABLE_DETECTED,
    EV_STATE_HANDSHAKE,
    EV_STATE_PRECHARGE,
    EV_STATE_CHARGING,
    EV_STATE_STOP

} EV_State_t;
EV_State_t ev_state = EV_STATE_IDLE;
//Parametre de la voiture
uint16_t battery_voltage_min = 0;      // V
uint16_t battery_voltage_max = 600;      // V
uint16_t battery_voltage = 360;      // V
uint16_t requested_current_max=200; // A
uint16_t requested_current;      // A
uint8_t soc=50;               // %
uint8_t charger_ready = 0;

uint8_t Maximum_charging_time;
uint8_t Estimated_charging_time;
uint16_t Total_capacity_of_battery;
uint16_t Target_battery_voltage;

uint8_t Battery_voltage_deviation_error =0;
uint8_t High_battery_temperature =0;
uint8_t Battery_current_deviation_error = 0;
uint8_t Battery_undervoltage =0;
uint8_t Battery_overvoltage =0;

uint8_t Normal_stop_request=0;
uint8_t Vehicle_status=0;
uint8_t Charging_system_error=0;
uint8_t Vehicle_shift_position=1;
uint8_t Vehicle_charging_enabled=0;


uint8_t Charging_stop_control = 1; // 0: operating, 1: during stop control or stop condition
uint8_t Energizing_state = 0;      // 0: disable, 1: enable
uint8_t Charger_error = 0;         // 0: normal, 1: error
uint8_t Charger_status = 0;		   // 0: standby, 1: charging
uint8_t Remaining_charging_time =0; // in the unit of 1 min

int Cable_Detected=0;

// Ajout des flags et buffers pour le traitement hors ISR
volatile uint8_t flag_msg_108 = 0;
volatile uint8_t flag_msg_109 = 0;
uint8_t rxData_108[8];
uint8_t rxData_109[8];


/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */
void CAN_Filter_Config(void);
void HAL_CAN_RxFifo0MsgPendingCallback(CAN_HandleTypeDef *hcan);
void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin);
void EV_send_parameters(uint16_t requested_current,uint16_t battery_voltage_min, uint16_t battery_voltage_max, uint8_t soc);
void EV_charge_time_soc(uint8_t Maximum_charging_time,uint8_t Estimated_charging_time, uint16_t Total_capacity_of_battery);
void EV_charge_control(void);
void EV_StateMachine(void);
void traitement_msg_108(void);
void traitement_msg_109(void);
/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
/* ----------------------------------------------------------
   STATION CAPABILITY LIMITS
   Battery MC checks MSG108 against these
   ---------------------------------------------------------- */
#define STATION_MAX_VOLTAGE   420
#define STATION_MIN_VOLTAGE   200
#define STATION_MAX_CURRENT   100

/* ----------------------------------------------------------
   UART RX — interrupt driven
   ---------------------------------------------------------- */
#define RX_BUF_SIZE 128

static uint8_t          rx_byte;
static char             rx_buf[RX_BUF_SIZE];
static uint16_t         rx_idx        = 0;
static char             rx_line[RX_BUF_SIZE];
static volatile uint8_t rx_line_ready = 0;

void UART_RX_Start(void)
{
    rx_idx        = 0;
    rx_line_ready = 0;
    HAL_UART_Receive_IT(&huart2, &rx_byte, 1);
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance != USART2) return;

    char c = (char)rx_byte;

    if (c == '\n')
    {
        if (rx_idx > 0 && rx_buf[rx_idx - 1] == '\r')
            rx_idx--;
        rx_buf[rx_idx] = '\0';
        strncpy(rx_line, rx_buf, RX_BUF_SIZE - 1);
        rx_line[RX_BUF_SIZE - 1] = '\0';
        rx_idx        = 0;
        rx_line_ready = 1;
    }
    else
    {
        if (rx_idx < RX_BUF_SIZE - 1)
            rx_buf[rx_idx++] = c;
    }

    HAL_UART_Receive_IT(&huart2, &rx_byte, 1);
}

/* ----------------------------------------------------------
   TX helper
   ---------------------------------------------------------- */
void UART_Send(const char *msg)
{
    HAL_UART_Transmit(&huart2, (uint8_t *)msg, strlen(msg), 100);
}

/* ----------------------------------------------------------
   Parser helper — returns the Nth comma-separated integer
   after the ':' in a message like "MSG100:5,280,400,0"
   ---------------------------------------------------------- */
static int get_field(const char *line, int index)
{
    const char *p = strchr(line, ':');
    if (!p) return 0;
    p++;
    for (int i = 0; i < index; i++)
    {
        p = strchr(p, ',');
        if (!p) return 0;
        p++;
    }
    return atoi(p);
}

/* ----------------------------------------------------------
   Wait for a complete line from UART
   Blocks until rx_line_ready is set by the interrupt
   ---------------------------------------------------------- */
static void wait_for_line(char *out)
{
    while (!rx_line_ready) { HAL_Delay(1); }

    __disable_irq();
    strncpy(out, rx_line, RX_BUF_SIZE - 1);
    out[RX_BUF_SIZE - 1] = '\0';
    rx_line_ready = 0;
    __enable_irq();
}

/* ----------------------------------------------------------
   CAN HELPER — Send a CAN frame
   ---------------------------------------------------------- */
static void CAN_Send_Frame(uint32_t id, uint8_t *data, uint8_t len)
{
    CAN_TxHeaderTypeDef tx_header;
    uint32_t tx_mailbox;

    tx_header.StdId = id;
    tx_header.IDE   = CAN_ID_STD;
    tx_header.RTR   = CAN_RTR_DATA;
    tx_header.DLC   = len;

    HAL_StatusTypeDef status = HAL_CAN_AddTxMessage(&hcan1, &tx_header, data, &tx_mailbox);

    if (status != HAL_OK)
    {
        UART_Send("[ERROR] CAN TX failed\r\n");
    }
}

/* ----------------------------------------------------------
   CAN HELPER — Wait and receive a CAN frame with specific ID
   Returns 1 if received, 0 if timeout (3 seconds)
   ---------------------------------------------------------- */
static uint8_t CAN_Wait_For_Frame(uint32_t expected_id, uint8_t *data_out, uint32_t timeout_ms)
{
    CAN_RxHeaderTypeDef rx_header;
    uint8_t rx_data[8] = {0};
    uint32_t start_tick = HAL_GetTick();

    while ((HAL_GetTick() - start_tick) < timeout_ms)
    {
        // Check if there's a message in FIFO0
        if (HAL_CAN_GetRxFifoFillLevel(&hcan1, CAN_RX_FIFO0) > 0)
        {
            if (HAL_CAN_GetRxMessage(&hcan1, CAN_RX_FIFO0, &rx_header, rx_data) == HAL_OK)
            {
                if (rx_header.StdId == expected_id)
                {
                    memcpy(data_out, rx_data, 8);
                    return 1;  // Success
                }
            }
        }
        HAL_Delay(10);  // Poll every 10ms
    }

    UART_Send("[ERROR] CAN RX timeout\r\n");
    return 0;  // Timeout
}

/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_USART2_UART_Init();
  MX_CAN1_Init();
  /* USER CODE BEGIN 2 */
  CAN_Filter_Config();
  HAL_CAN_Start(&hcan1);
  HAL_CAN_ActivateNotification(&hcan1, CAN_IT_RX_FIFO0_MSG_PENDING);
  //sprintf((char*)rxbuf,"EV_STATE_IDLE\r\n");

  UART_RX_Start();

     /* ── Variables for MSG100 ── */
     int min_current = 0;
     int min_voltage = 0;
     int max_voltage = 0;
     int charge_rate = 0;

     /* ── Variables for MSG101 ── */
     int max_time_10s  = 0;
     int max_time_1min = 0;
     int est_time      = 0;
     int capacity      = 0;

     /* ── Variables for MSG108 (received from station MC via CAN) ── */
     int welding_id    = 0;
     int avail_voltage = 0;
     int avail_current = 0;
     int threshold_v   = 0;

     char line[RX_BUF_SIZE];
     uint8_t msg100_ok = 0;
     uint8_t msg101_ok = 0;

  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
     while (1)
     {
       /* ======================================================
          PHASE 1 — Receive MSG100 and MSG101 from simulator
          ====================================================== */

       msg100_ok = 0;
       msg101_ok = 0;

       while (!msg100_ok || !msg101_ok)
       {
    	  HAL_GPIO_TogglePin(LD2_GPIO_Port, LD2_Pin);
    	  HAL_Delay(10);
    	  HAL_GPIO_TogglePin(LD2_GPIO_Port, LD2_Pin);

    	  wait_for_line(line);// ajouter un delais pour sortir de la fonction blocante



         /* ── Parse MSG100 ───────────────────────────────── */
         if (!msg100_ok && strncmp(line, "MSG100:", 7) == 0)
         {
           min_current = get_field(line, 0);
           min_voltage = get_field(line, 1);
           max_voltage = get_field(line, 2);
           charge_rate = get_field(line, 3);

           msg100_ok = 1;

           EV_send_parameters(min_current, min_voltage, max_voltage, charge_rate);

           while (!flag_msg_108)
           {
             HAL_Delay(1);
           }

           //if (flag_msg_108)
          // {
             traitement_msg_108();

          // }
             while (!flag_msg_109)
                        {
                          HAL_Delay(1);
                        }
             traitement_msg_109();
         }

         /* ── Parse MSG101 ───────────────────────────────── */
         if (!msg101_ok && strncmp(line, "MSG101:", 7) == 0)
         {
           max_time_10s = get_field(line, 0);
           max_time_1min = get_field(line, 1);
           est_time = get_field(line, 2);
           capacity = get_field(line, 3);

           msg101_ok = 1;

           EV_charge_time_soc(max_time_1min, est_time, capacity);

           while (!flag_msg_109)
           {
             HAL_Delay(1);
           }

           if (flag_msg_109)
           {
             traitement_msg_109();
             flag_msg_109 = 0;

           }
         }
       }

       /* ======================================================
          Compatibility check
          ====================================================== */

      /* if (Available_output_voltage >= battery_voltage_max &&
           Available_output_current >= requested_current &&
           Threshold_voltage <= battery_voltage_max)
       {
         UART_Send("ACK:COMPATIBLE\r\n");
       }
       else
       {
         UART_Send("ACK:INCOMPATIBLE\r\n");
         continue;
       }*/

       /* ======================================================
          PHASE 2 — Wait for MSG102 from simulator
          ====================================================== */

       uint8_t charging_active = !charger_ready;
       /*int flag_msg102=0;
       while(!flag_msg102)
       {

       wait_for_line(line);
       UART_Send("wait msg102 out the While\r\n");
                if (strncmp(line, "MSG102:", 7) == 0)
                {
                	flag_msg102=1;
                  Target_battery_voltage = get_field(line, 0);
                  requested_current = get_field(line, 1);
                  soc = get_field(line, 2);
                  Battery_voltage_deviation_error = get_field(line, 3);
                  Vehicle_charging_enabled = get_field(line, 4);
                  Normal_stop_request = get_field(line, 5);

                  EV_charge_control();
                  UART_Send("msg102 sent\r\n");
                }

       }*/
       while (charger_ready)
       {


         UART_Send("wait for msg102 in the while\r\n");
    	   wait_for_line(line);

         if (strncmp(line, "MSG102:", 7) == 0)
         {

           Target_battery_voltage = get_field(line, 0);
           requested_current = get_field(line, 1);
           soc = get_field(line, 2);
           Battery_voltage_deviation_error = get_field(line, 3);
           Vehicle_charging_enabled = get_field(line, 4);
           Normal_stop_request = get_field(line, 5);

           EV_charge_control();
           UART_Send("msg102 sent\r\n");

           while (!flag_msg_109)
           {
             HAL_Delay(1);
           }

           if (flag_msg_109)
           {
             traitement_msg_109();

           }
         }
           if (Normal_stop_request == 1 || soc == 100)
           {
             charging_active = 0;
             requested_current = 0;
             EV_charge_control();
             while (!flag_msg_109)
                      {
                        HAL_Delay(1);
                      }

                      if (flag_msg_109)
                      {
                        traitement_msg_109();

                      }
           }



       }
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
  CAN_FilterTypeDef filter;

  filter.FilterActivation = ENABLE;
  filter.FilterBank = 0;
  filter.FilterFIFOAssignment = CAN_FILTER_FIFO0;
  filter.FilterMode = CAN_FILTERMODE_IDMASK;
  filter.FilterScale = CAN_FILTERSCALE_32BIT;
  filter.FilterIdHigh = 0x0000;
  filter.FilterIdLow = 0x0000;
  filter.FilterMaskIdHigh = 0x0000;
  filter.FilterMaskIdLow = 0x0000;

  HAL_CAN_ConfigFilter(&hcan1, &filter);
}
//EV_send_parameters() permet d'envoyer les paramétres de charge de la batterie avec l'ID 0x100
void EV_send_parameters(uint16_t requested_current,uint16_t battery_voltage_min, uint16_t battery_voltage_max, uint8_t soc)
{
		CAN_TxHeaderTypeDef txHeader;
	    uint8_t txData[8];
	    uint32_t txMailbox;

	    txHeader.StdId = 0x100;
	    txHeader.IDE = CAN_ID_STD;
	    txHeader.RTR = CAN_RTR_DATA;
	    txHeader.DLC = 8;

	    txData[0] = (requested_current & 0xFF); //charge current
	    txData[1] = 0x00;//not used

	    txData[2] = (battery_voltage_min & 0xFF); // LSB minimum battery voltage
	    txData[3] = (battery_voltage_min >> 8);// MSB minimum battery voltage

	    txData[4] = (battery_voltage_max & 0xFF); // LSB maximum battery voltage
	    txData[5] = (battery_voltage_max >> 8);// MSB maximum battery voltage
	    txData[6] = soc;//Charged rete reference constant %_
	    txData[7] = 0x00; //not used

	    if(HAL_CAN_AddTxMessage(&hcan1, &txHeader, txData, &txMailbox) == HAL_OK)
	        {

	        	//HAL_GPIO_TogglePin(LD2_GPIO_Port, LD2_Pin);
	        	//HAL_Delay(10);
	        	//HAL_GPIO_TogglePin(LD2_GPIO_Port, LD2_Pin);
	        }
}
void EV_charge_time_soc(uint8_t Maximum_charging_time,uint8_t Estimated_charging_time, uint16_t Total_capacity_of_battery)
{
	CAN_TxHeaderTypeDef txHeader;
		    uint8_t txData[8];
		    uint32_t txMailbox;

		    txHeader.StdId = 0x101;
		    txHeader.IDE = CAN_ID_STD;
		    txHeader.RTR = CAN_RTR_DATA;
		    txHeader.DLC = 8;

		    txData[0] = 0x00;//not used
		    txData[1] = 0xFF;//not used

		    txData[2] = (Maximum_charging_time & 0xFF); // en minute
		    txData[3] = (Estimated_charging_time & 0xFF);// en minute
		    txData[4] = 0x00; //not used
		    txData[5] = (Total_capacity_of_battery & 0xFF);// unit 0.1 kWh LSB
		    txData[6] = (Total_capacity_of_battery >> 8);// MSB
		    txData[7] = 0x00; //not used

		    if(HAL_CAN_AddTxMessage(&hcan1, &txHeader, txData, &txMailbox) == HAL_OK)
		        {

		        	/*HAL_GPIO_TogglePin(LD2_GPIO_Port, LD2_Pin);
		        	HAL_Delay(10);
		        	HAL_GPIO_TogglePin(LD2_GPIO_Port, LD2_Pin);*/
		        }
}
void EV_charge_control(void)
{
	CAN_TxHeaderTypeDef txHeader;
			    uint8_t txData[8];
			    uint32_t txMailbox;

	 txHeader.StdId = 0x102;
	 txHeader.IDE = CAN_ID_STD;
	 txHeader.RTR = CAN_RTR_DATA;
	 txHeader.DLC = 8;

	 txData[0] = 0x03;//ChadeMo protocol ver.2.0
	 txData[1] = Target_battery_voltage & 0xFF;
	 txData[2] = Target_battery_voltage >> 8;
	 txData[3] = requested_current  & 0xFF;// Charging_current_request
	 txData[4] = ((Battery_voltage_deviation_error << 4) | High_battery_temperature <<3 |  Battery_current_deviation_error << 2 | Battery_undervoltage <<1 | Battery_overvoltage);
	 txData[5] = (Normal_stop_request<<4 | Vehicle_status<<3 | Charging_system_error<<2 | Vehicle_shift_position<<1 | Vehicle_charging_enabled );
	 txData[6] = soc;
	 txData[7] = 0x00; //not used

	 if(HAL_CAN_AddTxMessage(&hcan1, &txHeader, txData, &txMailbox) == HAL_OK)
	 {

		 HAL_GPIO_TogglePin(LD2_GPIO_Port, LD2_Pin);
		 //HAL_Delay(10);

	 }
}


void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin)
{
	Cable_Detected=1;
	Normal_stop_request=1;
}


void HAL_CAN_RxFifo0MsgPendingCallback(CAN_HandleTypeDef *hcan)
{
    CAN_RxHeaderTypeDef rxHeader;
    uint8_t tmpData[8];
           //HAL_GPIO_TogglePin(LD2_GPIO_Port, LD2_Pin);
           if(HAL_CAN_GetRxMessage(hcan, CAN_RX_FIFO0, &rxHeader, tmpData)==HAL_OK)
           {
           	if (rxHeader.StdId == 0x108)
           	        {
           	            // Copie des données et levée du flag — aucun traitement ici
           	            memcpy(rxData_108, tmpData, 8);
           	            flag_msg_108 = 1;
           	        }
           	if (rxHeader.StdId == 0x109)
           	        {
           	            memcpy(rxData_109, tmpData, 8);
           	            flag_msg_109 = 1;
           	        }
           	if (rxHeader.StdId == 0x7FF)
           	{
           	    UART_Send("RESET\r\n");
           	    HAL_Delay(10);           // give UART time to finish transmitting
           	    HAL_NVIC_SystemReset();  // full chip reset — cleans everything
           	}
           }
}

void traitement_msg_108(void)
{
	if(flag_msg_108) // Message de la voiture pour les parametres de charge
		     {
			 	 flag_msg_108 = 0;
			     //charger_ready = rxData[4];
		         Welding_detection = rxData_108[0] ;
		         Available_output_voltage =rxData_108[1] | (rxData_108[2]<<8);
		         Available_output_current =rxData_108[3];
		         Threshold_voltage = rxData_108[4] |  rxData_108[5]<<8;
		         	 	 	/* sprintf((char*)rxbuf,"Welding_detection %d \r\n",Welding_detection);
		                     HAL_UART_Transmit(&huart2, rxbuf, strlen((char*)rxbuf), 10);
		                     sprintf((char*)rxbuf,"Available_output_voltage %d V\r\n",Available_output_voltage);
		                     HAL_UART_Transmit(&huart2, rxbuf, strlen((char*)rxbuf), 10);
		                     sprintf((char*)rxbuf,"Available_output_current %d A\r\n",Available_output_current);
		                     HAL_UART_Transmit(&huart2, rxbuf, strlen((char*)rxbuf), 10);
		                     sprintf((char*)rxbuf,"Threshold_voltage %d\r\n",Threshold_voltage);
		                     HAL_UART_Transmit(&huart2, rxbuf, strlen((char*)rxbuf), 10);*/


		      }
	sprintf((char*)txbuf,
			"MSG108:%d,%d,%d,%d\r\n",
			Welding_detection,
			Available_output_voltage,Available_output_current,Threshold_voltage);

	HAL_UART_Transmit(&huart2, txbuf, strlen((char*)txbuf), 100);

}
void traitement_msg_109(void)
{
	if(flag_msg_109)
			 {
				 flag_msg_109 = 0;

				 charger_ready=rxData_109[5] & 0x01;   // 0: standby, 1: charging
				 Charger_error = (rxData_109[5] & (1<<1))>>1;         // 0: normal, 1: error
				 Energizing_state = (rxData_109[5] & (1<<2))>>2;// 0: disable, 1: enable
				 Battery_incompatibility=(rxData_109[5] & (1<<3))>>3;
				 Charging_system_error = (rxData_109[5] & (1<<4))>>4; // 0: normal, 1: error
				 Charging_stop_control = (rxData_109[5] & (1<<5))>>5; // 0: operating, 1: during stop control or stop condition

				 Remaining_charging_time =rxData_109[7]; // in the unit of 1 min
				 Present_output_voltage = rxData_109[1] | (rxData_109[2] << 8); //  present ouput voltage
				 Present_output_current = rxData_109[3];// present charging current

				 //MSG109:protocol_num,present_voltage,present_current,status_fault,remaining_10s,remaining_1min
				 //MSG109:protocol,pres_v,pres_i,fault,remaining_10s,remaining_1min,incompat
				 sprintf((char*)txbuf,
						 "MSG109:%d,%d,%d,%d,%d,%d,%d\r\n",
						 rxData_109[0],Present_output_voltage,Present_output_current,
						 charger_ready,rxData_109[6],Remaining_charging_time,Battery_incompatibility);

				 HAL_UART_Transmit(&huart2, txbuf, strlen((char*)txbuf), 100);



			 }
}
void EV_StateMachine(void)
{

    switch(ev_state)
    {
        case EV_STATE_IDLE:
        	HAL_UART_Transmit(&huart2, rxbuf, strlen((char*)rxbuf), 1000);
            if(Cable_Detected)
            {

            	Cable_Detected=0;
                ev_state = EV_STATE_CABLE_DETECTED;
                sprintf((char*)rxbuf,"EV_STATE_CABLE_DETECTED\r\n");
                //HAL_Delay(1000);
            }

        break;

        case EV_STATE_CABLE_DETECTED:
        	HAL_UART_Transmit(&huart2, rxbuf, strlen((char*)rxbuf), 1000);
        	HAL_Delay(1000);
            requested_current = 0;
            //EV_send_parameters();
            ev_state = EV_STATE_HANDSHAKE;
            sprintf((char*)rxbuf,"EV_STATE_HANDSHAKE\r\n");
        break;

        case EV_STATE_HANDSHAKE:
        	HAL_UART_Transmit(&huart2, rxbuf, strlen((char*)rxbuf), 1000);
        	HAL_Delay(1000);

            if(!Battery_incompatibility && !charger_ready)
            {
            	EV_charge_control();
            	ev_state = EV_STATE_PRECHARGE;
                sprintf((char*)rxbuf,"EV_STATE_PRECHARGE\r\n");
            }
            //EV_charge_time_soc();


        break;

        case EV_STATE_PRECHARGE:
        	HAL_UART_Transmit(&huart2, rxbuf, strlen((char*)rxbuf), 1000);
        	HAL_Delay(1000);
            if(battery_voltage > 300)
            {
                ev_state = EV_STATE_CHARGING;
                sprintf((char*)rxbuf,"EV_STATE_CHARGING\r\n");
            }
            EV_charge_control();
        break;

        case EV_STATE_CHARGING:
        	HAL_UART_Transmit(&huart2, rxbuf, strlen((char*)rxbuf), 1000);
            requested_current = 100;  // 100A example
            HAL_Delay(1000);
            soc += 10;
            EV_charge_control();

            if(soc >= 100)
            {
                ev_state = EV_STATE_STOP;
                sprintf((char*)rxbuf,"EV_STATE_STOP\r\n");
            }
        break;

        case EV_STATE_STOP:
        	HAL_UART_Transmit(&huart2, rxbuf, strlen((char*)rxbuf), 1000);
        	HAL_Delay(1000);
            requested_current = 0;
            EV_charge_control();
            ev_state = EV_STATE_IDLE;
            sprintf((char*)rxbuf,"EV_STATE_IDLE\r\n");
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
