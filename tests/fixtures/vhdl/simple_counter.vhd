-- Simple counter module for testing Vivado synthesis
-- This is a basic design to verify the synthesis pipeline

library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.NUMERIC_STD.ALL;

entity simple_counter is
    Generic (
        WIDTH : integer := 8
    );
    Port (
        clk     : in  STD_LOGIC;
        rst     : in  STD_LOGIC;
        enable  : in  STD_LOGIC;
        count   : out STD_LOGIC_VECTOR(WIDTH-1 downto 0)
    );
end simple_counter;

architecture Behavioral of simple_counter is
    signal counter_reg : unsigned(WIDTH-1 downto 0) := (others => '0');
begin

    process(clk, rst)
    begin
        if rst = '1' then
            counter_reg <= (others => '0');
        elsif rising_edge(clk) then
            if enable = '1' then
                counter_reg <= counter_reg + 1;
            end if;
        end if;
    end process;

    count <= std_logic_vector(counter_reg);

end Behavioral;
