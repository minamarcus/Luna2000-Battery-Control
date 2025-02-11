from typing import List, Dict, Optional
from pymodbus.client import ModbusTcpClient
from config import logger, TOU_REGISTER, PORT

class BatteryManager:
    def __init__(self, host: str, port: int = PORT):
        self.host = host
        self.port = port
        self.TOU_REGISTER = TOU_REGISTER

    def connect(self) -> Optional[ModbusTcpClient]:
        """Establish connection to the battery."""
        try:
            client = ModbusTcpClient(self.host)
            if not client.connect():
                raise ConnectionError("Failed to connect to battery")
            logger.info(f"Successfully connected to battery at {self.host}:{self.port}")
            return client
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return None

    def _decode_flags(self, flag_value: int) -> tuple:
        """
        Decode the combined flags value into charge flag and day bits.
        Values examples:
        4 = Tuesday and charging
        260 = Tuesday and discharging (4 + 256)
        """
        charge_flag = 1 if flag_value >= 256 else 0
        day_bits = flag_value & 0x7F  # Get only the day bits (0-127)
        return charge_flag, day_bits

    def _encode_flags(self, charge_flag: int, day_bits: int) -> int:
        """
        Encode charge flag and day bits into a single value.
        charge_flag: 0=charge, 1=discharge
        day_bits: day value (1=Sunday, 2=Monday, 4=Tuesday, etc.)
        Returns:
        - Just day_bits for charging (e.g., 4 for Tuesday)
        - day_bits + 256 for discharging (e.g., 260 for Tuesday)
        """
        return day_bits + (256 if charge_flag == 1 else 0)

    def _parse_schedule(self, data: List[int]) -> Dict:
        """Parse raw register data into a structured format."""
        num_periods = data[0]
        periods = []

        for i in range(num_periods):
            base_idx = 1 + (i * 3)  # Each period takes 3 values
            if base_idx + 2 >= len(data):
                break

            start_time = data[base_idx]
            end_time = data[base_idx + 1]
            period_flags = data[base_idx + 2]
            
            # Decode the combined flags
            charge_flag, day_bits = self._decode_flags(period_flags)

            periods.append({
                'start_time': start_time,
                'end_time': end_time,
                'charge_flag': charge_flag,
                'days': day_bits,
                'is_charging': charge_flag == 0
            })

        return {
            'num_periods': num_periods,
            'periods': periods,
            'raw_data': data
        }

    def read_schedule(self) -> Optional[Dict]:
        """Read and parse the battery schedule."""
        client = None
        try:
            client = self.connect()
            if not client:
                return None

            response = client.read_holding_registers(
                address=self.TOU_REGISTER,
                count=43,
                slave=1
            )

            if response.isError():
                logger.error(f"Error reading register: {response}")
                return None

            data = list(response.registers)
            return self._parse_schedule(data)

        except Exception as e:
            logger.error(f"Error reading schedule: {e}")
            return None
        finally:
            if client:
                client.close()

    def get_soc(self) -> Optional[float]:
        """
        Get the current State of Charge (SOC) of the battery.
        Returns:
            float: Battery SOC in percentage (0-100) or None if read fails
        """
        client = None
        try:
            client = self.connect()
            if not client:
                return None

            response = client.read_holding_registers(
                address=37760,  # SOC register address
                count=1,
                slave=1
            )

            if response.isError():
                logger.error(f"Error reading SOC register: {response}")
                return None

            # Convert raw value using gain of 10
            soc = float(response.registers[0]) / 10.0
            logger.info(f"Current battery SOC: {soc}%")
            return soc

        except Exception as e:
            logger.error(f"Error reading SOC: {e}")
            return None
        finally:
            if client:
                client.close()

    def write_schedule(self, data: List[int]) -> bool:
        """Write schedule to battery."""
        client = None
        try:
            client = self.connect()
            if not client:
                return False

            if len(data) != 43:
                raise ValueError(f"Data must be exactly 43 values, got {len(data)}")

            response = client.write_registers(
                address=self.TOU_REGISTER,
                values=data,
                slave=1
            )

            if response.isError():
                raise Exception(f"Error writing to register: {response}")

            logger.info("Successfully wrote schedule to battery")
            return True

        except Exception as e:
            logger.error(f"Error writing schedule: {e}")
            return False
        finally:
            if client:
                client.close()