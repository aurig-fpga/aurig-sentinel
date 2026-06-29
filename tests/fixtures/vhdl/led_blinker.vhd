-- LED Blinker module for Vivado synthesis test
-- Generates a simple blink pattern

library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.NUMERIC_STD.ALL;

entity led_blinker is
    Generic (
        CLK_FREQ    : integer := 100_000_000;  -- 100 MHz
        BLINK_FREQ  : integer := 1             -- 1 Hz
    );
    Port (
        clk         : in  STD_LOGIC;
        rst         : in  STD_LOGIC;
        led_out     : out STD_LOGIC
    );
end led_blinker;

architecture RTL of led_blinker is

    constant COUNT_MAX : integer := CLK_FREQ / (2 * BLINK_FREQ);
    signal counter : integer range 0 to COUNT_MAX := 0;
    signal led_reg : std_logic := '0';

begin

    process(clk, rst)
    begin
        if rst = '1' then
            counter <= 0;
            led_reg <= '0';
        elsif rising_edge(clk) then
            if counter = COUNT_MAX - 1 then
                counter <= 0;
                led_reg <= not led_reg;
            else
                counter <= counter + 1;
            end if;
        end if;
    end process;

    led_out <= led_reg;

end RTL;
